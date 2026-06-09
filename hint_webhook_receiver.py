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

# ─── Configuration ─────────────────────────────────

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

# ─── Email configuration ────────────────────────────────
# SMTP via Google Workspace (care@mtbakermedical.com).
# Requires a Google App Password (not the login password):
#   myaccount.google.com → Security → 2-Step Verification → App passwords
#
# Environment variables:
#   RESEND_API_KEY   API key from resend.com (required for live sends)
#   FROM_EMAIL       Sending address (default: care@mtbakermedical.com)
#   REVIEW_BASE_URL  Public URL of this receiver, e.g. https://xyz.onrender.com
#                    Used to build the ?fname= link sent in the email.

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Mt. Baker Medical <care@mtbakermedical.com>")
REVIEW_BASE_URL = os.environ.get("REVIEW_BASE_URL", "https://mtbakermedical.com")

# --- Spruce SMS configuration -------------------------------------------------
# Spruce Health (the practice's BAA-signed HIPAA-compliant communication platform)
# Used to send the review-request as an SMS in parallel with the email.
#
# Get the API key:  Spruce dashboard -> Settings -> Integrations & API -> API Access
# Get the endpoint: Spruce dashboard -> Settings -> Phone System (or call
#                   GET /v1/internalendpoints and pick the channel of type "phone")
#
# Both must be set for SMS to send; otherwise SMS is silently skipped and the
# existing email path still works.

SPRUCE_API_KEY = os.environ.get("SPRUCE_API_KEY", "")
SPRUCE_INTERNAL_ENDPOINT_ID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
SPRUCE_BASE_URL = "https://api.sprucehealth.com/v1"

# --- Frequency cap configuration ----------------------------------------------
# Each patient is asked for a review at most MAX_REQUESTS_PER_PATIENT times,
# with at least MIN_DAYS_BETWEEN_REQUESTS days between asks. Tracked locally in
# patient_state.json on Render's persistent disk.

MAX_REQUESTS_PER_PATIENT = 3
MIN_DAYS_BETWEEN_REQUESTS = 30
PATIENT_STATE_FILE = SCRIPT_DIR / "patient_state.json"

# ─── Logging ───────────────────────────────────

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

# ─── Flask app ─────────────────────────────────────

app = Flask(__name__)

# ─── Signature verification ──────────────────────────────

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

# ─── Patient lookup ────────────────────────────────

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

def extract_phi_minimal(patient: dict) -> tuple[str, str, str] | None:
    """
    Extract ONLY first_name + email + mobile_phone from a patient record.
    Discards all other fields (last_name, DOB, conditions, MRN, etc.).
    Returns (first_name, email, phone) or None if BOTH email AND phone are missing.

    PHI boundary: these three fields are the only ones passed to the delivery
    layer. All other patient data is discarded. Per CLAUDE.md §2.1.

    Either email or phone may be empty; at least one is required to dispatch.
    """
    first_name = (patient.get("first_name") or "").strip()
    email = (patient.get("email") or "").strip()

    # Hint stores phones as a list of {number, type} objects under "phones".
    # Prefer mobile/cell types (SMS-capable); fall back to first available.
    phone = ""
    phones = patient.get("phones") or []
    if isinstance(phones, list):
        # Prefer mobile/cell
        for p in phones:
            if isinstance(p, dict):
                ptype = (p.get("type") or "").lower()
                if "mobile" in ptype or "cell" in ptype:
                    candidate = (p.get("number") or "").strip()
                    if candidate:
                        phone = candidate
                        break
        # Fallback: first non-empty number of any type
        if not phone:
            for p in phones:
                if isinstance(p, dict):
                    candidate = (p.get("number") or "").strip()
                    if candidate:
                        phone = candidate
                        break

    # Legacy flat-field fallback (older Hint payload shape, just in case)
    if not phone:
        phone = (
            patient.get("mobile_phone")
            or patient.get("phone")
            or patient.get("home_phone")
            or ""
        ).strip()

    if not email and not phone:
        return None
    return first_name, email, phone

# ─── Email delivery ──────────────────────────────────

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

    if not RESEND_API_KEY:
        log.warning(f"RESEND_API_KEY not set — skipping email to {email}.")
        return False

    try:
        resp = http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": FROM_EMAIL,
                "to": [email],
                "subject": _EMAIL_SUBJECT.format(first_name=greeting_name),
                "html": html_body,
                "text": text_body,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info(f"Review email sent to {email} (link={review_link})")
            return True
        else:
            log.error(f"Resend returned {resp.status_code}: {resp.text}")
            return False

    except Exception as e:
        log.error(f"Failed to send review email to {email}: {e}")
        return False



# --- Spruce SMS delivery ------------------------------------------------------

def send_review_sms(first_name: str, phone: str) -> bool:
    """Send a review-request SMS via Spruce. Returns True on success."""
    name = first_name.strip() if first_name else "there"
    name_param = urllib.parse.quote(name) if name and name != "there" else ""
    if name_param:
        review_link = f"{REVIEW_BASE_URL}/review?fname={name_param}"
    else:
        review_link = f"{REVIEW_BASE_URL}/review"

    body = (
        f"Hi {name}, this is Mt. Baker Medical. "
        "Thanks for being a patient. "
        "James was wondering if you'd mind sharing your experience? "
        "Honest reviews - whatever you'd say - help the next person find care that fits. "
        f"{review_link} "
        "Reply DONE if you've already reviewed, STOP to opt out."
    )

    phone_tail = phone[-4:] if len(phone) >= 4 else "????"

    if DRY_RUN:
        log.info(f"[DRY_RUN] Would SMS phone ending {phone_tail}")
        return True

    if not SPRUCE_API_KEY or not SPRUCE_INTERNAL_ENDPOINT_ID:
        log.warning(
            "Spruce not configured (SPRUCE_API_KEY/SPRUCE_INTERNAL_ENDPOINT_ID) "
            f"-- skipping SMS to phone ending {phone_tail}"
        )
        return False

    try:
        url = f"{SPRUCE_BASE_URL}/internalendpoints/{SPRUCE_INTERNAL_ENDPOINT_ID}/conversations"
        resp = http.post(
            url,
            headers={
                "Authorization": f"Bearer {SPRUCE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "destination": {
                    "smsOrEmailEndpoint": {"endpoint": phone},
                },
                "message": {"text": body},
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info(f"Review SMS sent to phone ending {phone_tail}")
            return True
        log.error(
            f"Spruce API returned {resp.status_code} sending to phone ending "
            f"{phone_tail}: {resp.text[:200]}"
        )
        return False
    except Exception as e:
        log.error(f"Failed to send SMS to phone ending {phone_tail}: {e}")
        return False


# --- Patient state (30-day spacing + 3-lifetime-cap) --------------------------
# Stored in patient_state.json on Render's persistent disk. Schema:
#   { "<patient_id>": { "count": <int>, "last_ask_ts": "<ISO 8601 UTC>" } }
# No PHI in this file -- only Hint opaque IDs and timestamps.

def _read_patient_state() -> dict:
    try:
        if PATIENT_STATE_FILE.exists():
            return json.loads(PATIENT_STATE_FILE.read_text())
        return {}
    except Exception as e:
        log.warning(f"Could not read patient_state.json: {e}")
        return {}


def _write_patient_state(state: dict):
    try:
        PATIENT_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception as e:
        log.error(f"Could not write patient_state.json: {e}")


def _should_send_review_request(patient_id: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Reason is human-readable for log lines."""
    state = _read_patient_state()
    patient = state.get(patient_id, {})
    count = patient.get("count", 0)
    last_ask_ts = patient.get("last_ask_ts")

    if count >= MAX_REQUESTS_PER_PATIENT:
        return False, f"cap-hit count={count}/{MAX_REQUESTS_PER_PATIENT}"

    if last_ask_ts:
        try:
            last_dt = datetime.fromisoformat(last_ask_ts.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            if days_since < MIN_DAYS_BETWEEN_REQUESTS:
                return (
                    False,
                    f"spacing-not-met {days_since}d since last ask "
                    f"(count={count}/{MAX_REQUESTS_PER_PATIENT})",
                )
        except Exception as e:
            log.warning(f"Could not parse last_ask_ts={last_ask_ts}: {e}")

    return True, f"ok count={count}/{MAX_REQUESTS_PER_PATIENT}"


def _record_request_sent(patient_id: str):
    """Increment the patient's request count and update last_ask_ts to now."""
    state = _read_patient_state()
    patient = state.get(patient_id, {"count": 0, "last_ask_ts": None})
    patient["count"] = patient.get("count", 0) + 1
    patient["last_ask_ts"] = datetime.now(timezone.utc).isoformat()
    state[patient_id] = patient
    _write_patient_state(state)


# ─── State helpers ───────────────────────────────────

def _update_last_webhook_ts():
    """Update last_webhook_received in bridge_state.json."""
    state_file = SCRIPT_DIR / "bridge_state.json"
    try:
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state["last_webhook_received"] = datetime.now(timezone.utc).isoformat()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning(f"Could not update last_webhook_received: {e}")


# ─── Event handlers ────────────────────────────────

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
    # Email/phone may or may not be present — attempt direct extraction first
    phi = extract_phi_minimal(obj)
    if phi:
        first_name, email, phone = phi
        log.info(
            f"patient.created (direct): patient_id={patient_id} "
            f"email={'yes' if email else 'no'} phone={'yes' if phone else 'no'}"
        )
        _dispatch_to_bridge(
            patient_id, first_name, email, phone, trigger="patient.created"
        )
    else:
        # No email or phone in payload — fall back to patient API lookup
        log.info(
            f"patient.created: no contact fields in payload, fetching patient {patient_id}"
        )
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
        log.warning(
            f"[{trigger}] Patient {patient_id} has no email or phone — skipping"
        )
        return

    first_name, email, phone = phi
    _dispatch_to_bridge(patient_id, first_name, email, phone, trigger=trigger)


def _dispatch_to_bridge(
    patient_id: str,
    first_name: str,
    email: str,
    phone: str,
    trigger: str,
):
    """
    Send a review-request via both email (Resend) and SMS (Spruce), subject to
    the 30-day-spacing + 3-lifetime-cap rules tracked in patient_state.json.

    Either channel may be skipped if the patient is missing that contact field;
    cap/spacing applies to the patient (not per channel).
    """
    allowed, reason = _should_send_review_request(patient_id)
    if not allowed:
        log.info(f"[{trigger}] SKIP patient_id={patient_id}: {reason}")
        return

    log.info(
        f"[{trigger}] Processing: patient_id={patient_id} "
        f"email={'yes' if email else 'no'} phone={'yes' if phone else 'no'} "
        f"dry_run={DRY_RUN} ({reason})"
    )

    email_ok = send_review_email(first_name=first_name, email=email) if email else False
    sms_ok = send_review_sms(first_name=first_name, phone=phone) if phone else False

    email_status = "ok" if email_ok else ("skip" if not email else "failed")
    sms_status = "ok" if sms_ok else ("skip" if not phone else "failed")
    log.info(f"[{trigger}] Delivery: email={email_status} sms={sms_status}")

    if email_ok or sms_ok:
        _record_request_sent(patient_id)
        new_count = _read_patient_state().get(patient_id, {}).get("count")
        log.info(
            f"[{trigger}] Recorded: patient_id={patient_id} "
            f"count={new_count}/{MAX_REQUESTS_PER_PATIENT}"
        )


# ─── Webhook endpoint ───────────────────────────────────────────

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
        "from_email": FROM_EMAIL,
        "resend_configured": bool(RESEND_API_KEY),
        "spruce_configured": bool(SPRUCE_API_KEY) and bool(SPRUCE_INTERNAL_ENDPOINT_ID),
        "partner_key_set": bool(HINT_PARTNER_API_KEY),
        "practices_key_set": bool(HINT_API_KEY),
        "max_requests_per_patient": MAX_REQUESTS_PER_PATIENT,
        "min_days_between_requests": MIN_DAYS_BETWEEN_REQUESTS,
        "tracked_patients": len(_read_patient_state()),
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

    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — cannot send feedback alert email")
        return

    try:
        resp = http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": FROM_EMAIL,
                "to": [FEEDBACK_ALERT_TO],
                "subject": subject,
                "html": body_html,
                "text": body_text,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            log.info(f"Feedback alert sent to {FEEDBACK_ALERT_TO}")
        else:
            log.error(f"Resend returned {resp.status_code}: {resp.text}")
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
    log.info(f"Email: from={FROM_EMAIL} resend_configured={bool(RESEND_API_KEY)}")
    _sms_configured = bool(SPRUCE_API_KEY) and bool(SPRUCE_INTERNAL_ENDPOINT_ID)
    log.info(
        f"SMS:   spruce_configured={_sms_configured} "
        f"(api_key={'yes' if SPRUCE_API_KEY else 'no'}, "
        f"endpoint_id={'yes' if SPRUCE_INTERNAL_ENDPOINT_ID else 'no'})"
    )
    log.info(
        f"Cap:   max_requests_per_patient={MAX_REQUESTS_PER_PATIENT} "
        f"min_days_between={MIN_DAYS_BETWEEN_REQUESTS}"
    )
    log.info(f"Review base URL: {REVIEW_BASE_URL}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
