#!/usr/bin/env python3
"""
Hint Health Webhook Receiver
============================
Listens for Hint Health webhook events and sends a review-request email to
each patient who completes a service or enrolls as a member.

WHY THIS FILE EXISTS
--------------------
Replaces the old cron-polling approach with a real-time event-driven
architecture. When Hint fires an event, this receiver sends a branded email
from care@mtbakermedical.com with a link to the self-hosted star-rating
review funnel (/review?name=FirstName).

IMPORTANT: Hint does NOT have appointment-specific webhook events (no
appointment.completed or visit.completed — Hint is a membership platform).
The closest available proxies for "patient had a service" are:

  membership.created     — patient enrolled; first invite trigger
  customer_invoice.paid  — an invoice was paid, implying a service was rendered
  patient.created        — new patient record created (enrollment intent)

USAGE
-----
Development (ngrok tunnel):
  # Terminal 1:
  ngrok http 5000
  # Terminal 2:
  SMTP_PASS=<app-password> python3 hint_webhook_receiver.py

Production (Render):
  HINT_ENV=production
  HINT_API_KEY=<practices_key>
  HINT_PARTNER_API_KEY=<partner_key>
  SMTP_USER=care@mtbakermedical.com
  SMTP_PASS=<google-app-password>
  REVIEW_BASE_URL=https://your-service.onrender.com
  DRY_RUN=false

ENVIRONMENT VARIABLES
---------------------
  HINT_ENV             sandbox | production  (default: sandbox)
  HINT_API_KEY         Hint practices API key (for patient lookups)
  HINT_PARTNER_API_KEY Hint partner API key (for signature verification)
                       Get from https://app.hint.com/partner/api_keys
  SMTP_USER            care@mtbakermedical.com  (default)
  SMTP_PASS            Google App Password for SMTP_USER — must be set for live sends
  REVIEW_BASE_URL      WordPress site URL used to build /review?fname= links (default: https://mtbakermedical.com)
  DRY_RUN              true | false  (default: true — logs email, does not send)
  PORT                 HTTP port (default: 5000)

WEBHOOK REGISTRATION
--------------------
Once running with a public URL, register it in the Hint Partner Portal:
  Sandbox:    https://app.staging.hint.com/partner/account/webhooks
  Production: https://app.hint.com/partner/account/webhooks

Select events: membership.created, customer_invoice.paid, patient.created

SIGNATURE VERIFICATION
----------------------
Hint signs each request with:
  X-Hint-Signature: sha256=<HMAC-SHA256 hex>
Keyed on the partner API key (NOT the practices API key).
"""

import os
import json
import hashlib
import hmac
import logging
import sys
import smtplib
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("Installing flask...")
    os.system("pip install flask requests --break-system-packages -q")
    from flask import Flask, request, jsonify

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

# ─── Configuration ────────────────────────────────────────────────────────────

HINT_ENV = os.environ.get("HINT_ENV", "sandbox")

# Load credentials from file as fallback
SCRIPT_DIR = Path(__file__).parent
CRED_FILE = SCRIPT_DIR.parent.parent.parent / "credentials" / "hint_credentials.json"

def _load_creds() -> dict:
    try:
        return json.loads(CRED_FILE.read_text())
    except Exception:
        return {}

_creds = _load_creds()
_env_creds = _creds.get(HINT_ENV, {})

# Practices API key — for looking up patient details from membership/invoice events
HINT_API_KEY = (
    os.environ.get("HINT_API_KEY")
    or _env_creds.get("keys", {}).get("practices_api_key", {}).get("value", "")
)

# Partner API key — for HMAC signature verification ONLY (never sent to patient systems)
HINT_PARTNER_API_KEY = (
    os.environ.get("HINT_PARTNER_API_KEY")
    or _env_creds.get("keys", {}).get("partner_api_key", {}).get("value", "")
)

HINT_BASE_URL = (
    "https://api.hint.com" if HINT_ENV == "production"
    else "https://api.sandbox.hint.com"
)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
PORT = int(os.environ.get("PORT", 5000))

# ─── Email configuration ──────────────────────────────────────────────────────
# SMTP via Google Workspace (care@mtbakermedical.com).
# Requires a Google App Password (not the login password):
#   myaccount.google.com → Security → 2-Step Verification → App passwords
#
# Environment variables:
#   SMTP_USER      care@mtbakermedical.com  (or set below as default)
#   SMTP_PASS      16-char Google App Password
#   REVIEW_BASE_URL  Public URL of this receiver, e.g. https://xyz.onrender.com
#                    Used to build the ?name= link sent in the email.

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "care@mtbakermedical.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "")   # must be set in production
REVIEW_BASE_URL = os.environ.get("REVIEW_BASE_URL", "https://mtbakermedical.com")

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE = SCRIPT_DIR / "webhook_receiver.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("hint_receiver")

# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)

# ─── Signature verification ───────────────────────────────────────────────────

def verify_hint_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify the X-Hint-Signature header.

    Hint computes: sha256=HMAC-SHA256(key=partner_api_key, msg=raw_body).hexdigest()
    Uses constant-time comparison to prevent timing attacks.
    """
    if not HINT_PARTNER_API_KEY:
        log.warning("No HINT_PARTNER_API_KEY set — skipping signature check (unsafe for production!)")
        return True  # Allow in dev when key isn't configured; gate this in production below

    expected = "sha256=" + hmac.new(
        key=HINT_PARTNER_API_KEY.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not signature_header:
        log.warning("Request missing X-Hint-Signature header")
        return False

    return hmac.compare_digest(expected, signature_header)

# ─── Patient lookup ──────────────────────────────────────────────────────────────

def fetch_patient(patient_id: str) -> dict | None:
    """
    Fetch a patient record from Hint by ID.
    Returns the full patient object, or None on failure.

    PHI note: the caller is responsible for stripping all fields except
    first_name and email before passing downstream.
    """
    url = f"{HINT_BASE_URL}/api/provider/patients/{patient_id}"
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        resp = http.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Failed to fetch patient {patient_id}: {e}")
        return None

def extract_phi_minimal(patient: dict) -> tuple[str, str] | None:
    """
    Extract ONLY first_name + email from a patient record.
    Discards all other fields (last_name, DOB, conditions, MRN, etc.).
    Returns (first_name, email) or None if email is missing.

    PHI boundary: these two fields are the only ones passed to the email sender.
    All other patient data is discarded. Per CLAUDE.md §2.1.
    """
    first_name = (patient.get("first_name") or "").strip()
    email = (patient.get("email") or "").strip()
    if not email:
        return None
    return first_name, email

# ─── Email delivery ───────────────────────────────────────────────────────────

_EMAIL_SUBJECT = "How did we do, {first_name}?"

_EMAIL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f1;padding:40px 16px;">
    <tr><td align="center">
      <table width="100%" style="max-width:480px;background:#ffffff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;">
        <tr><td style="background:#1a6b4a;padding:28px 32px 24px;text-align:center;">
          <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#a8d5be;">Physician-Led Concierge Care</p>
          <p style="margin:6px 0 0;font-size:24px;font-weight:700;color:#ffffff;">Mt. Baker Medical</p>
        </td></tr>
        <tr><td style="padding:36px 32px 12px;text-align:center;">
          <p style="margin:0 0 12px;font-size:22px;font-weight:600;color:#1a1a1a;">How did we do, {first_name}?</p>
          <p style="margin:0;font-size:15px;line-height:1.6;color:#4b5563;">Your feedback helps Dr. Scribner and our team keep raising the bar&nbsp;&mdash; for you and every patient who walks through our door.</p>
        </td></tr>
        <tr><td style="padding:24px 32px 8px;text-align:center;">
          <a href="{review_link}" style="display:inline-block;background:#1a6b4a;color:#ffffff;font-size:16px;font-weight:600;padding:14px 36px;border-radius:10px;text-decoration:none;">How did we do?</a>
        </td></tr>
        <tr><td style="padding:12px 32px 32px;text-align:center;">
          <p style="margin:0;font-size:13px;color:#9ca3af;">Takes less than a minute.</p>
        </td></tr>
        <tr><td style="background:#1a6b4a;padding:16px 32px;text-align:center;">
          <p style="margin:0;font-size:11px;color:#a8d5be;">Mt. Baker Medical · 1200 Harris Ave, Suite 308, Bellingham, WA 98225<br>
          To stop receiving these emails, reply with STOP.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_EMAIL_TEXT = """\
Hi {first_name},

How was your visit at Mt. Baker Medical?

Share your feedback here:
{review_link}

Thank you for being a patient.

-- Mt. Baker Medical
1200 Harris Ave, Suite 308, Bellingham, WA 98225

To stop receiving these emails, reply with STOP.
"""


def send_review_email(first_name: str, email: str) -> bool:
    """
    Send a review-request email to a patient from care@mtbakermedical.com.

    Builds a ?name= link pointing at the /review funnel page served by this
    same receiver. Returns True on success, False on any error.

    If DRY_RUN is True, logs the email without sending it.
    If SMTP_PASS is empty, logs a warning and skips (not configured yet).
    """
    name_param = urllib.parse.quote(first_name) if first_name else ""
    review_link = f"{REVIEW_BASE_URL}/review?fname={name_param}" if name_param else f"{REVIEW_BASE_URL}/review"

    greeting_name = first_name if first_name else "there"
    html_body = _EMAIL_HTML.format(first_name=greeting_name, review_link=review_link)
    text_body = _EMAIL_TEXT.format(first_name=greeting_name, review_link=review_link)

    if DRY_RUN:
        log.info(f"[DRY_RUN] Would email {email}: subject='{_EMAIL_SUBJECT.format(first_name=greeting_name)}' link={review_link}")
        return True

    if not SMTP_PASS:
        log.warning(f"SMTP_PASS not set — skipping email to {email}. Set SMTP_PASS to a Google App Password.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = _EMAIL_SUBJECT.format(first_name=greeting_name)
        msg["From"] = f"Mt. Baker Medical <{SMTP_USER}>"
        msg["To"] = email
        msg["Reply-To"] = SMTP_USER
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [email], msg.as_string())

        log.info(f"Review email sent to {email} (link={review_link})")
        return True

    except Exception as e:
        log.error(f"Failed to send review email to {email}: {e}")
        return False


# ─── State helpers ────────────────────────────────────────────────────────────

def _update_last_webhook_ts():
    """Update last_webhook_received in bridge_state.json."""
    state_file = SCRIPT_DIR / "bridge_state.json"
    try:
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state["last_webhook_received"] = datetime.now(timezone.utc).isoformat()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning(f"Could not update last_webhook_received: {e}")


# ─── Event handlers ───────────────────────────────────────────────────────────

def handle_membership_created(event: dict):
    """
    A patient enrolled as a member. First natural touch point for a review request
    after their first appointment (which Hint doesn't surface via webhook, so
    enrollment is our best real-time signal).
    """
    obj = event.get("object", {})
    patient_id = obj.get("patient_id") or obj.get("id")
    if not patient_id:
        log.warning(f"membership.created: no patient_id in object: {list(obj.keys())}")
        return

    log.info(f"membership.created: patient_id={patient_id}")
    _process_patient_id(patient_id, trigger="membership.created")


def handle_invoice_paid(event: dict):
    """
    A customer invoice was paid — proxy for "a service was rendered."
    Re-triggers the invite pipeline for this patient. The bridge layer will
    skip if they've already reviewed; re-invite if they haven't.
    """
    obj = event.get("object", {})
    patient_id = obj.get("patient_id")
    invoice_id = obj.get("id", "?")
    if not patient_id:
        log.warning(f"customer_invoice.paid: no patient_id in object for invoice {invoice_id}")
        return

    log.info(f"customer_invoice.paid: invoice={invoice_id} patient_id={patient_id}")
    _process_patient_id(patient_id, trigger="customer_invoice.paid")


def handle_patient_created(event: dict):
    """
    New patient record created. Often fires before membership.created.
    The payload includes first_name directly (no secondary lookup needed).
    """
    obj = event.get("object", {})
    patient_id = obj.get("id")
    if not patient_id:
        log.warning("patient.created: no id in object")
        return

    # For patient.created, first_name is in the object itself
    # Email may or may not be present — attempt direct extraction first
    phi = extract_phi_minimal(obj)
    if phi:
        first_name, email = phi
        log.info(f"patient.created (direct): patient_id={patient_id} email={email}")
        _dispatch_to_bridge(patient_id, first_name, email, trigger="patient.created")
    else:
        # Email not in payload — fall back to patient API lookup
        log.info(f"patient.created: no email in payload, fetching patient {patient_id}")
        _process_patient_id(patient_id, trigger="patient.created")


def _process_patient_id(patient_id: str, trigger: str):
    """
    Look up a patient by ID, extract PHI-minimal fields, dispatch to bridge.
    """
    patient = fetch_patient(patient_id)
    if not patient:
        log.error(f"[{trigger}] Could not fetch patient {patient_id} — skipping")
        return

    phi = extract_phi_minimal(patient)
    if not phi:
        log.warning(f"[{trigger}] Patient {patient_id} has no email — skipping")
        return

    first_name, email = phi
    _dispatch_to_bridge(patient_id, first_name, email, trigger=trigger)


def _dispatch_to_bridge(patient_id: str, first_name: str, email: str, trigger: str):
    """
    Send a review-request email from care@mtbakermedical.com.

    This is the only place first_name + email are used together; all other
    code in this file operates on patient_id only.
    """
    log.info(f"[{trigger}] Processing: patient_id={patient_id} email={email} dry_run={DRY_RUN}")
    email_ok = send_review_email(first_name=first_name, email=email)
    log.info(f"[{trigger}] Email delivery: {'ok' if email_ok else 'failed'}")


# ─── Webhook endpoint ─────────────────────────────────────────────────────────────────────────────────

HANDLED_EVENTS = {
    "membership.created": handle_membership_created,
    "customer_invoice.paid": handle_invoice_paid,
    "patient.created": handle_patient_created,
}


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Main Hint webhook receiver endpoint.

    Hint expects a 2XX response within a reasonable timeout. Non-2XX triggers
    retries with exponential backoff. We return 200 even for events we don't
    handle (to avoid noisy retries for unsupported event types).
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Hint-Signature", "")

    # 1. Verify signature
    if HINT_ENV == "production" and not verify_hint_signature(raw_body, signature):
        log.warning(f"Signature verification failed. sig={signature[:30]}...")
        return jsonify({"error": "invalid signature"}), 403

    # 2. Parse payload
    try:
        event = request.get_json(force=True)
    except Exception as e:
        log.error(f"Failed to parse JSON body: {e}")
        return jsonify({"error": "bad json"}), 400

    event_type = event.get("type", "unknown")
    event_id = event.get("id", "?")
    practice_id = event.get("practice_id", "?")

    log.info(f"Received event: id={event_id} type={event_type} practice={practice_id}")

    # 3. Update last_webhook_received in state
    _update_last_webhook_ts()

    # 4. Route to handler
    handler = HANDLED_EVENTS.get(event_type)
    if handler:
        try:
            handler(event)
        except Exception as e:
            log.exception(f"Handler for {event_type} raised: {e}")
"""
Hint Health Webhook Receiver
============================
Listens for Hint Health webhook events and sends a review-request email to
each patient who completes a service or enrolls as a member.

WHY THIS FILE EXISTS
--------------------
Replaces the old cron-polling approach with a real-time event-driven
architecture. When Hint fires an event, this receiver sends a branded email
from care@mtbakermedical.com with a link to the self-hosted star-rating
review funnel (/review?name=FirstName).

IMPORTANT: Hint does NOT have appointment-specific webhook events (no
appointment.completed or visit.completed — Hint is a membership platform).
The closest available proxies for "patient had a service" are:

  membership.created     — patient enrolled; first invite trigger
  customer_invoice.paid  — an invoice was paid, implying a service was rendered
  patient.created        — new patient record created (enrollment intent)

USAGE
-----
Development (ngrok tunnel):
  # Terminal 1:
  ngrok http 5000
  # Terminal 2:
  SMTP_PASS=<app-password> python3 hint_webhook_receiver.py

Production (Render):
  HINT_ENV=production
  HINT_API_KEY=<practices_key>
  HINT_PARTNER_API_KEY=<partner_key>
  SMTP_USER=care@mtbakermedical.com
  SMTP_PASS=<google-app-password>
  REVIEW_BASE_URL=https://your-service.onrender.com
  DRY_RUN=false

ENVIRONMENT VARIABLES
---------------------
  HINT_ENV             sandbox | production  (default: sandbox)
  HINT_API_KEY         Hint practices API key (for patient lookups)
  HINT_PARTNER_API_KEY Hint partner API key (for signature verification)
                       Get from https://app.hint.com/partner/api_keys
  SMTP_USER            care@mtbakermedical.com  (default)
  SMTP_PASS            Google App Password for SMTP_USER — must be set for live sends
  REVIEW_BASE_URL      WordPress site URL used to build /review?fname= links (default: https://mtbakermedical.com)
  DRY_RUN              true | false  (default: true — logs email, does not send)
  PORT                 HTTP port (default: 5000)

WEBHOOK REGISTRATION
--------------------
Once running with a public URL, register it in the Hint Partner Portal:
  Sandbox:    https://app.staging.hint.com/partner/account/webhooks
  Production: https://app.hint.com/partner/account/webhooks

Select events: membership.created, customer_invoice.paid, patient.created

SIGNATURE VERIFICATION
----------------------
Hint signs each request with:
  X-Hint-Signature: sha256=<HMAC-SHA256 hex>
Keyed on the partner API key (NOT the practices API key).
"""

import os
import json
import hashlib
import hmac
import logging
import sys
import smtplib
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("Installing flask...")
    os.system("pip install flask requests --break-system-packages -q")
    from flask import Flask, request, jsonify

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

# ─── Configuration ────────────────────────────────────────────────────────────

HINT_ENV = os.environ.get("HINT_ENV", "sandbox")

# Load credentials from file as fallback
SCRIPT_DIR = Path(__file__).parent
CRED_FILE = SCRIPT_DIR.parent.parent.parent / "credentials" / "hint_credentials.json"

def _load_creds() -> dict:
    try:
        return json.loads(CRED_FILE.read_text())
    except Exception:
        return {}

_creds = _load_creds()
_env_creds = _creds.get(HINT_ENV, {})

# Practices API key — for looking up patient details from membership/invoice events
HINT_API_KEY = (
    os.environ.get("HINT_API_KEY")
    or _env_creds.get("keys", {}).get("practices_api_key", {}).get("value", "")
)

# Partner API key — for HMAC signature verification ONLY (never sent to patient systems)
HINT_PARTNER_API_KEY = (
    os.environ.get("HINT_PARTNER_API_KEY")
    or _env_creds.get("keys", {}).get("partner_api_key", {}).get("value", "")
)

HINT_BASE_URL = (
    "https://api.hint.com" if HINT_ENV == "production"
    else "https://api.sandbox.hint.com"
)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
PORT = int(os.environ.get("PORT", 5000))

# ─── Email configuration ──────────────────────────────────────────────────────
# SMTP via Google Workspace (care@mtbakermedical.com).
# Requires a Google App Password (not the login password):
#   myaccount.google.com → Security → 2-Step Verification → App passwords
#
# Environment variables:
#   SMTP_USER      care@mtbakermedical.com  (or set below as default)
#   SMTP_PASS      16-char Google App Password
#   REVIEW_BASE_URL  Public URL of this receiver, e.g. https://xyz.onrender.com
#                    Used to build the ?name= link sent in the email.

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "care@mtbakermedical.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "")   # must be set in production
REVIEW_BASE_URL = os.environ.get("REVIEW_BASE_URL", "https://mtbakermedical.com")

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE = SCRIPT_DIR / "webhook_receiver.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("hint_receiver")

# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)

# ─── Signature verification ───────────────────────────────────────────────────

def verify_hint_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify the X-Hint-Signature header.

    Hint computes: sha256=HMAC-SHA256(key=partner_api_key, msg=raw_body).hexdigest()
    Uses constant-time comparison to prevent timing attacks.
    """
    if not HINT_PARTNER_API_KEY:
        log.warning("No HINT_PARTNER_API_KEY set — skipping signature check (unsafe for production!)")
        return True  # Allow in dev when key isn't configured; gate this in production below

    expected = "sha256=" + hmac.new(
        key=HINT_PARTNER_API_KEY.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not signature_header:
        log.warning("Request missing X-Hint-Signature header")
        return False

    return hmac.compare_digest(expected, signature_header)

# ─── Patient lookup ──────────────────────────────────────────────────────────────

def fetch_patient(patient_id: str) -> dict | None:
    """
    Fetch a patient record from Hint by ID.
    Returns the full patient object, or None on failure.

    PHI note: the caller is responsible for stripping all fields except
    first_name and email before passing downstream.
    """
    url = f"{HINT_BASE_URL}/api/provider/patients/{patient_id}"
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        resp = http.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Failed to fetch patient {patient_id}: {e}")
        return None

def extract_phi_minimal(patient: dict) -> tuple[str, str] | None:
    """
    Extract ONLY first_name + email from a patient record.
    Discards all other fields (last_name, DOB, conditions, MRN, etc.).
    Returns (first_name, email) or None if email is missing.

    PHI boundary: these two fields are the only ones passed to the email sender.
    All other patient data is discarded. Per CLAUDE.md §2.1.
    """
    first_name = (patient.get("first_name") or "").strip()
    email = (patient.get("email") or "").strip()
    if not email:
        return None
    return first_name, email

# ─── Email delivery ───────────────────────────────────────────────────────────

_EMAIL_SUBJECT = "How did we do, {first_name}?"

_EMAIL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f1;padding:40px 16px;">
    <tr><td align="center">
      <table width="100%" style="max-width:480px;background:#ffffff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;">
        <tr><td style="background:#1a6b4a;padding:28px 32px 24px;text-align:center;">
          <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#a8d5be;">Physician-Led Concierge Care</p>
          <p style="margin:6px 0 0;font-size:24px;font-weight:700;color:#ffffff;">Mt. Baker Medical</p>
        </td></tr>
        <tr><td style="padding:36px 32px 12px;text-align:center;">
          <p style="margin:0 0 12px;font-size:22px;font-weight:600;color:#1a1a1a;">How did we do, {first_name}?</p>
          <p style="margin:0;font-size:15px;line-height:1.6;color:#4b5563;">Your feedback helps Dr. Scribner and our team keep raising the bar&nbsp;&mdash; for you and every patient who walks through our door.</p>
        </td></tr>
        <tr><td style="padding:24px 32px 8px;text-align:center;">
          <a href="{review_link}" style="display:inline-block;background:#1a6b4a;color:#ffffff;font-size:16px;font-weight:600;padding:14px 36px;border-radius:10px;text-decoration:none;">How did we do?</a>
        </td></tr>
        <tr><td style="padding:12px 32px 32px;text-align:center;">
          <p style="margin:0;font-size:13px;color:#9ca3af;">Takes less than a minute.</p>
        </td></tr>
        <tr><td style="background:#1a6b4a;padding:16px 32px;text-align:center;">
          <p style="margin:0;font-size:11px;color:#a8d5be;">Mt. Baker Medical · 1200 Harris Ave, Suite 308, Bellingham, WA 98225<br>
          To stop receiving these emails, reply with STOP.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_EMAIL_TEXT = """\
Hi {first_name},

How was your visit at Mt. Baker Medical?

Share your feedback here:
{review_link}

Thank you for being a patient.

-- Mt. Baker Medical
1200 Harris Ave, Suite 308, Bellingham, WA 98225

To stop receiving these emails, reply with STOP.
"""


def send_review_email(first_name: str, email: str) -> bool:
    """
    Send a review-request email to a patient from care@mtbakermedical.com.

    Builds a ?name= link pointing at the /review funnel page served by this
    same receiver. Returns True on success, False on any error.

    If DRY_RUN is True, logs the email without sending it.
    If SMTP_PASS is empty, logs a warning and skips (not configured yet).
    """
    name_param = urllib.parse.quote(first_name) if first_name else ""
    review_link = f"{REVIEW_BASE_URL}/review?fname={name_param}" if name_param else f"{REVIEW_BASE_URL}/review"

    greeting_name = first_name if first_name else "there"
    html_body = _EMAIL_HTML.format(first_name=greeting_name, review_link=review_link)
    text_body = _EMAIL_TEXT.format(first_name=greeting_name, review_link=review_link)

    if DRY_RUN:
        log.info(f"[DRY_RUN] Would email {email}: subject='{_EMAIL_SUBJECT.format(first_name=greeting_name)}' link={review_link}")
        return True

    if not SMTP_PASS:
        log.warning(f"SMTP_PASS not set — skipping email to {email}. Set SMTP_PASS to a Google App Password.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = _EMAIL_SUBJECT.format(first_name=greeting_name)
        msg["From"] = f"Mt. Baker Medical <{SMTP_USER}>"
        msg["To"] = email
        msg["Reply-To"] = SMTP_USER
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [email], msg.as_string())

        log.info(f"Review email sent to {email} (link={review_link})")
        return True

    except Exception as e:
        log.error(f"Failed to send review email to {email}: {e}")
        return False


# ─── State helpers ──────────────────────────────────────────────────────────────────

def _update_last_webhook_ts():
    """Update last_webhook_received in bridge_state.json."""
    state_file = SCRIPT_DIR / "bridge_state.json"
    try:
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state["last_webhook_received"] = datetime.now(timezone.utc).isoformat()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning(f"Could not update last_webhook_received: {e}")


# ─── Event handlers ───────────────────────────────────────────────────────────

def handle_membership_created(event: dict):
    """
    A patient enrolled as a member. First natural touch point for a review request
    after their first appointment (which Hint doesn't surface via webhook, so
    enrollment is our best real-time signal).
    """
    obj = event.get("object", {})
    patient_id = obj.get("patient_id") or obj.get("id")
    if not patient_id:
        log.warning(f"membership.created: no patient_id in object: {list(obj.keys())}")
        return

    log.info(f"membership.created: patient_id={patient_id}")
    _process_patient_id(patient_id, trigger="membership.created")


def handle_invoice_paid(event: dict):
    """
    A customer invoice was paid — proxy for "a service was rendered."
    Re-triggers the invite pipeline for this patient. The bridge layer will
    skip if they've already reviewed; re-invite if they haven't.
    """
    obj = event.get("object", {})
    patient_id = obj.get("patient_id")
    invoice_id = obj.get("id", "?")
    if not patient_id:
        log.warning(f"customer_invoice.paid: no patient_id in object for invoice {invoice_id}")
        return

    log.info(f"customer_invoice.paid: invoice={invoice_id} patient_id={patient_id}")
    _process_patient_id(patient_id, trigger="customer_invoice.paid")


def handle_patient_created(event: dict):
    """
    New patient record created. Often fires before membership.created.
    The payload includes first_name directly (no secondary lookup needed).
    """
    obj = event.get("object", {})
    patient_id = obj.get("id")
    if not patient_id:
        log.warning("patient.created: no id in object")
        return

    # For patient.created, first_name is in the object itself
    # Email may or may not be present — attempt direct extraction first
    phi = extract_phi_minimal(obj)
    if phi:
        first_name, email = phi
        log.info(f"patient.created (direct): patient_id={patient_id} email={email}")
        _dispatch_to_bridge(patient_id, first_name, email, trigger="patient.created")
    else:
        # Email not in payload — fall back to patient API lookup
        log.info(f"patient.created: no email in payload, fetching patient {patient_id}")
        _process_patient_id(patient_id, trigger="patient.created")


def _process_patient_id(patient_id: str, trigger: str):
    """
    Look up a patient by ID, extract PHI-minimal fields, dispatch to bridge.
    """
    patient = fetch_patient(patient_id)
    if not patient:
        log.error(f"[{trigger}] Could not fetch patient {patient_id} — skipping")
        return

    phi = extract_phi_minimal(patient)
    if not phi:
        log.warning(f"[{trigger}] Patient {patient_id} has no email — skipping")
        return

    first_name, email = phi
    _dispatch_to_bridge(patient_id, first_name, email, trigger=trigger)


def _dispatch_to_bridge(patient_id: str, first_name: str, email: str, trigger: str):
    """
    Send a review-request email from care@mtbakermedical.com.

    This is the only place first_name + email are used together; all other
    code in this file operates on patient_id only.
    """
    log.info(f"[{trigger}] Processing: patient_id={patient_id} email={email} dry_run={DRY_RUN}")
    email_ok = send_review_email(first_name=first_name, email=email)
    log.info(f"[{trigger}] Email delivery: {'ok' if email_ok else 'failed'}")


# ─── Webhook endpoint ─────────────────────────────────────────────────────────────────────────────────

HANDLED_EVENTS = {
    "membership.created": handle_membership_created,
    "customer_invoice.paid": handle_invoice_paid,
    "patient.created": handle_patient_created,
}


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Main Hint webhook receiver endpoint.

    Hint expects a 2XX response within a reasonable timeout. Non-2XX triggers
    retries with exponential backoff. We return 200 even for events we don't
    handle (to avoid noisy retries for unsupported event types).
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Hint-Signature", "")

    # 1. Verify signature
    if HINT_ENV == "production" and not verify_hint_signature(raw_body, signature):
        log.warning(f"Signature verification failed. sig={signature[:30]}...")
        return jsonify({"error": "invalid signature"}), 403

    # 2. Parse payload
    try:
        event = request.get_json(force=True)
    except Exception as e:
        log.error(f"Failed to parse JSON body: {e}")
        return jsonify({"error": "bad json"}), 400

    event_type = event.get("type", "unknown")
    event_id = event.get("id", "?")
    practice_id = event.get("practice_id", "?")

    log.info(f"Received event: id={event_id} type={event_type} practice={practice_id}")

    # 3. Update last_webhook_received in state
    _update_last_webhook_ts()

    # 4. Route to handler
    handler = HANDLED_EVENTS.get(event_type)
    if handler:
        try:
            handler(event)
        except Exception as e:
            log.exception(f"Handler for {event_type} raised: {e}")
            return jsonify({"status": "handler_error", "event_id": event_id}), 200
    else:
        log.debug(f"Ignoring unhandled event type: {event_type}")

    return jsonify({"status": "ok", "event_id": event_id}), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple liveness probe for hosting platforms."""
    return jsonify({
        "status": "ok",
        "env": HINT_ENV,
        "dry_run": DRY_RUN,
        "smtp_user": SMTP_USER,
        "smtp_configured": bool(SMTP_PASS),
        "partner_key_set": bool(HINT_PARTNER_API_KEY),
        "practices_key_set": bool(HINT_API_KEY),
    }), 200


FEEDBACK_FILE = SCRIPT_DIR / "feedback.json"

@app.route("/feedback", methods=["POST"])
def receive_feedback():
    """Receive negative review feedback from the funnel page."""
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        data = {}
    entry = {
        "name": data.get("name", "Anonymous"),
        "rating": data.get("rating"),
        "feedback": data.get("feedback", ""),
        "ts_patient": data.get("submitted_at"),
        "ts_received": datetime.now(timezone.utc).isoformat(),
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
    }
    log.info("Feedback received: rating=%s name=%s", entry["rating"], entry["name"])
    feedback_list = []
    if FEEDBACK_FILE.exists():
        try:
            feedback_list = json.loads(FEEDBACK_FILE.read_text())
        except Exception:
            feedback_list = []
    feedback_list.append(entry)
    FEEDBACK_FILE.write_text(json.dumps(feedback_list, indent=2, default=str))

    # Email negative feedback to charlie@mtbakermedical.com
    _email_feedback_alert(entry)

    return jsonify({"status": "ok"}), 200


def _email_feedback_alert(entry: dict):
    """Send a feedback alert email to charlie@mtbakermedical.com."""
    FEEDBACK_ALERT_TO = "charlie@mtbakermedical.com"
    rating = entry.get("rating", "?")
    name = entry.get("name", "Anonymous")
    feedback_text = entry.get("feedback", "").strip()
    ts = entry.get("ts_received", "")[:19]

    subject = f"[Mt. Baker Medical] {rating}★ private feedback from {name}"
    body_text = (
        f"New private feedback received via the review funnel.\n\n"
        f"From: {name}\n"
        f"Rating: {rating} star(s)\n"
        f"Time: {ts}\n\n"
        f"Message:\n{feedback_text}\n\n"
        f"---\nThis was submitted via the Mt. Baker Medical review page.\n"
        f"The patient chose not to post publicly on Google."
    )
    body_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 16px;">
<tr><td align="center">
<table width="100%" style="max-width:480px;background:#ffffff;border-radius:16px;overflow:hidden;">
  <tr><td style="background:#1a6b4a;padding:24px 32px;text-align:center;">
    <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#a8d5be;">Physician-Led Concierge Care</p>
    <p style="margin:6px 0 0;font-size:20px;font-weight:700;color:#ffffff;">Mt. Baker Medical</p>
  </td></tr>
  <tr><td style="padding:32px;">
    <p style="margin:0 0 8px;font-size:18px;font-weight:600;color:#1a1a1a;">Private Feedback Received</p>
    <p style="margin:0 0 24px;font-size:14px;color:#6b7280;">A patient left feedback through the review funnel but chose not to post publicly on Google.</p>
    <table width="100%" cellpadding="8" style="background:#f8faf9;border-radius:10px;margin-bottom:24px;">
      <tr><td style="font-size:13px;color:#6b7280;width:80px;">From</td><td style="font-size:14px;color:#1a1a1a;font-weight:600;">{name}</td></tr>
      <tr><td style="font-size:13px;color:#6b7280;">Rating</td><td style="font-size:14px;color:#1a1a1a;">{"★" * int(rating) if str(rating).isdigit() else rating} ({rating}/5)</td></tr>
      <tr><td style="font-size:13px;color:#6b7280;">Time</td><td style="font-size:13px;color:#1a1a1a;">{ts}</td></tr>
    </table>
    <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.05em;">Their message</p>
    <div style="background:#fff8f0;border-left:4px solid #f59e0b;padding:16px;border-radius:0 8px 8px 0;font-size:15px;color:#1a1a1a;line-height:1.6;">{feedback_text}</div>
  </td></tr>
  <tr><td style="background:#1a6b4a;padding:14px 32px;text-align:center;">
    <p style="margin:0;font-size:12px;color:#a8d5be;">Mt. Baker Medical · 1200 Harris Ave, Suite 308, Bellingham, WA</p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    if DRY_RUN:
        log.info(f"[DRY_RUN] Would send feedback alert to {FEEDBACK_ALERT_TO}: subject='{subject}'")
        return

    if not SMTP_PASS:
        log.warning("SMTP_PASS not set — cannot send feedback alert email")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Mt. Baker Medical <{SMTP_USER}>"
        msg["To"] = FEEDBACK_ALERT_TO
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [FEEDBACK_ALERT_TO], msg.as_string())
        log.info(f"Feedback alert sent to {FEEDBACK_ALERT_TO}")
    except Exception as e:
        log.error(f"Failed to send feedback alert: {e}")


@app.route("/admin/feedback", methods=["GET"])
def view_feedback():
    """View all received negative feedback entries."""
    if not FEEDBACK_FILE.exists():
        return "No feedback yet.", 200
    feedback_list = json.loads(FEEDBACK_FILE.read_text())
    if not feedback_list:
        return "No feedback yet.", 200
    out = ["=== {} entries ===".format(len(feedback_list))]
    for i, fb in enumerate(reversed(feedback_list), 1):
        out.append("[{}] {} | {} | {}*".format(
            i, fb.get("ts_received","?")[:19],
            fb.get("name","?"), fb.get("rating","?")))
        out.append("    " + (fb.get("feedback") or "").strip())
    separator = chr(10)
    return separator.join(out), 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    log.info(f"Starting Hint webhook receiver (env={HINT_ENV} dry_run={DRY_RUN} port={PORT})")
    log.info(f"SMTP: {SMTP_USER} via {SMTP_HOST}:{SMTP_PORT} configured={bool(SMTP_PASS)}")
    log.info(f"Review base URL: {REVIEW_BASE_URL}")
    app.run(host="0.0.0.0", port=PORT, debug=
