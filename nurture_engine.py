#!/usr/bin/env python3
"""
nurture_engine.py - Shared logic for Mt. Baker Medical's post-consult nurture
sequence.

  T5b (2026-06-10): first build. Trigger was a Spruce `nurture-prospect` tag.
  T5c (2026-06-10): trigger = staff-assigned FUTURE-DATED PENDING Hint membership
                    with NO payment source. Tag path retired.
  T5d (2026-06-10): per-plan signup LINKS on Day 0; cross-record (duplicate)
                    suppression + conversion reconcile (auto-cancel the stale
                    staff-created pending membership); Day-30 end-of-sequence
                    cleanup (cancel membership + staff archive list; NEVER delete);
                    audit trail.

Sender: practice Spruce line (360) 295-9241, signed "James". Copy is service-
agnostic (D-006 attorney gate); the per-plan element is the LINK only. Day 21 STOP
verbatim.

GUARDRAILS: does NOT import the review/consult-intake pollers; never writes
patient_state.json (review state); own state file + own schedule + one Task
Scheduler job. All Hint WRITES (cancel) are gated by DRY_RUN.
"""

import os
import csv
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

# --- Config (reuses the review poller's .env) --------------------------------
SCRIPT_DIR = Path(__file__).parent

SPRUCE_API_KEY = os.environ.get("SPRUCE_API_KEY", "")
SPRUCE_INTERNAL_ENDPOINT_ID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
SPRUCE_BASE_URL = "https://api.sprucehealth.com/v1"

HINT_ENV = os.environ.get("HINT_ENV", "production")
HINT_API_KEY = os.environ.get("HINT_API_KEY", "")
HINT_BASE_URL = (
    "https://api.hint.com" if HINT_ENV == "production"
    else "https://api.sandbox.hint.com"
)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

# --- Nurture constants -------------------------------------------------------
NURTURE_STATE_FILE = SCRIPT_DIR / "nurture_state.json"
REVIEW_STATE_FILE = SCRIPT_DIR / "patient_state.json"      # READ-ONLY here
AUDIT_FILE = SCRIPT_DIR / "nurture_audit.log"              # append-only audit
ARCHIVE_QUEUE_FILE = SCRIPT_DIR / "nurture_archive_queue.csv"  # weekly staff list
SEQUENCE_DAYS = [0, 7, 21]
CLEANUP_DAY = 28          # legacy fixed day-count; retained for old snapshots/tests. Live cleanup now keys off bill_date.
CLEANUP_LEAD_DAYS = 2     # cancel a stale, unpaid pending membership when its real bill_date is <= this many days away
APPT_LOOKAHEAD_DAYS = 45  # "future appointment" guard window
OFFICE_LINE = "(360) 498-7529"

# Hint cancellation_reason ids (from GET /api/provider/cancellation_reasons;
# verified live 2026-06-11). cancellation_reason must be {"id": <cnr-...>} -
# there is no "Other". Reconcile = a conversion landed elsewhere -> "Switched
# plans"; Day-30 cleanup of a non-converter -> "Contract expired".
CANCEL_REASON_RECONCILE = "cnr-XDiXnv3xSgom"  # Switched plans
CANCEL_REASON_CLEANUP = "cnr-nQZGCIlgkXEl"    # Contract expired

# Per-plan signup URLs (Part A). Keyed by Hint plan id. Plans absent here fall
# back to the link-free copy. Concierge 2026 reuses the existing live default
# page (verified to require Patient Agreement + Disclosure + Fee Schedule + HIPAA
# in T5/T5c). Other plans' pages are created in Hint admin and added here.
PLAN_SIGNUP_URLS = {
    "pln-xjukKCU9Xf6M": "https://mtbakermedical.hint.com/signup/concierge",                # Concierge 2026
    "pln-mjB9MEZD5bio": "https://mtbakermedical.hint.com/signup/so-glp-1-semaglutide",       # SO GLP-1 Semaglutide
    "pln-V8YNuahExamp": "https://mtbakermedical.hint.com/signup/so-glp-1-tirzepatide",       # SO GLP-1 Tirzepatide
    "pln-vVp3WOwlYuyO": "https://mtbakermedical.hint.com/signup/so-hormone-focused-care-hrt",  # SO HRT
    "pln-uj91OwP5xH4D": "https://mtbakermedical.hint.com/signup/so-trt-weekly-injection",    # SO TRT Weekly Injection
    "pln-SVPYWPV612po": "https://mtbakermedical.hint.com/signup/so-trt-topical-cream",       # SO TRT Topical Cream
    "pln-dByXpvwlpFyg": "https://mtbakermedical.hint.com/signup/so-ketamine-therapy",        # SO - Ketamine Therapy (IV-only CSA published + required-on-signup 2026-06-11)
    "pln-lN2eJKDLrUPy": "https://mtbakermedical.hint.com/signup/concierge-medicare-couple",       # Concierge - Medicare Beneficiary Couple (per person)
    "pln-vKXyMzt13o6P": "https://mtbakermedical.hint.com/signup/concierge-medicare-beneficiary",  # Concierge - Medicare Beneficiary (individual)
    "pln-MHIc5hNWtfhk": "https://mtbakermedical.hint.com/signup/concierge-2026-couple",           # Concierge 2026 - Couple (per person)
}

# --- Copy --------------------------------------------------------------------
# Day 0 has a link variant (used when the plan has a usable signup URL) and a
# link-free fallback. Day 7/21 stay link-free. Day 21 STOP line is VERBATIM.
DAY0_LINK = (
    "Hi {first_name}, this is James from Mt. Baker Medical. I really enjoyed "
    "meeting you today. When you're ready to get started, here's your signup "
    "link: {url}. If any questions come up, just reply and it comes straight to "
    "me. Talk soon."
)
NURTURE_TEXTS_LINKFREE = {
    0: (
        "Hi {first_name}, this is James from Mt. Baker Medical. I really enjoyed "
        "meeting you today. Whenever you're ready to get started, just reply here "
        f"or call the office at {OFFICE_LINE} and we'll send your enrollment "
        "paperwork. Any questions, reply anytime, it comes straight to me. Talk soon."
    ),
    7: (
        "Hi {first_name}, James here, just checking in. Any questions about how "
        "things work, or whether it's the right fit for you? Reply anytime, I'm "
        "glad to help. Whenever you're ready, just reply or call us and we'll get "
        "your enrollment started."
    ),
    21: (
        "Hi {first_name}, James again. I'll keep the door open whenever you're "
        "ready, no pressure at all. Just reply whenever you'd like to get started. "
        "It was a real pleasure meeting you. Reply STOP to opt out."
    ),
}

# --- Plan-family branching (2026-07) -----------------------------------------
# Only Concierge individual + couple get the enriched 5-touch sequence with
# price-accurate copy. Medicare + Service-Only stay on the agnostic 3-touch
# (respects the "no Medicare language" rule + the D-006 service-agnostic gate).
# Day 0 (link/link-free) and the verbatim Day 21 STOP are shared by all families.
CONCIERGE_IND_PLANS = {"pln-xjukKCU9Xf6M"}        # Concierge 2026 (individual)
CONCIERGE_COUPLE_PLANS = {"pln-MHIc5hNWtfhk"}     # Concierge 2026 - Couple
SEQUENCE_DAYS_CONCIERGE = [0, 3, 7, 14, 21]
NURTURE_EXCLUDE_PLANS = {"pln-BhgiC3jP0yzq"}  # Friends & Family $0 comp - never nurture or auto-cancel

NURTURE_TEXTS_CONCIERGE_IND = {
    3: (
        "Hi {first_name}, James here. A fair question after our visit: is $300/mo "
        "worth it? Honestly, it buys real time with your doctor, same-week access, "
        "no copays, and no insurance to fight, with care built around keeping you "
        f"well instead of a rushed visit months out. Whenever you're ready, just "
        f"reply or call {OFFICE_LINE}."
    ),
    7: (
        "Hi {first_name}, James checking in. If it helps to picture it: your first "
        "visit is a full 60 to 90 minutes, your history, your goals, and thorough "
        "labs, then a plan that's actually yours, with me a text away as things "
        "come up. Glad to answer anything, just reply or call us when you're ready."
    ),
    14: (
        "Hi {first_name}, one more thought. A lot of our members join as couples, "
        "since the care works best when it's both of you, and it's $570/mo for the "
        "two of you. If your partner would want in, I'm glad to fold them in. "
        "Either way, just reply whenever you're ready."
    ),
}
NURTURE_TEXTS_CONCIERGE_COUPLE = {
    3: (
        "Hi {first_name}, James here. A fair question after our visit: is $570/mo "
        "for the two of you worth it? Honestly, it buys real time with your doctor "
        "for both of you, same-week access, no copays, and no insurance to fight, "
        f"with care built around keeping you well. Whenever you're ready, just "
        f"reply or call {OFFICE_LINE}."
    ),
    7: (
        "Hi {first_name}, James checking in. If it helps to picture it: your first "
        "visits run a full 60 to 90 minutes each, your history, your goals, and "
        "thorough labs for both of you, then a plan that's yours, with me a text "
        "away as things come up. Glad to answer anything, just reply or call us "
        "when you're ready."
    ),
    14: (
        "Hi {first_name}, if it's mostly a matter of lining up two schedules, that "
        "part's easy. We can enroll you both together and book your first visits "
        f"back to back. Whenever you're ready, just reply or call {OFFICE_LINE}."
    ),
}


def nurture_family(plan_id):
    if plan_id in CONCIERGE_IND_PLANS:
        return "concierge_ind"
    if plan_id in CONCIERGE_COUPLE_PLANS:
        return "concierge_couple"
    return "agnostic"


def sequence_days_for(plan_id):
    if nurture_family(plan_id) in ("concierge_ind", "concierge_couple"):
        return SEQUENCE_DAYS_CONCIERGE
    return SEQUENCE_DAYS


def texts_for(plan_id):
    """Day->template map for this plan. Concierge families override days 3/7/14;
    everyone else gets the agnostic 0/7/21 copy. Day 0 (linkfree) + Day 21 always
    come from NURTURE_TEXTS_LINKFREE so the verbatim STOP line is never altered."""
    fam = nurture_family(plan_id)
    if fam == "concierge_ind":
        return {**NURTURE_TEXTS_LINKFREE, **NURTURE_TEXTS_CONCIERGE_IND}
    if fam == "concierge_couple":
        return {**NURTURE_TEXTS_LINKFREE, **NURTURE_TEXTS_CONCIERGE_COUPLE}
    return NURTURE_TEXTS_LINKFREE


DENYLIST_NAME_SUBSTRINGS = [
    "zz-test", "zztest", "nurtureqa", "nurturecheck", "donotcontact", "do-not-contact",
]
OPT_OUT_KEYWORDS = {
    "stop", "stopall", "unsubscribe", "cancel", "end", "quit",
    "optout", "opt-out", "revoke", "remove",
}

# --- Logging -----------------------------------------------------------------
LOG_FILE = SCRIPT_DIR / "nurture_engine.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("nurture_engine")


# --- Copied helper (NOT imported from the review poller) ---------------------
def normalize_phone_e164(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


def norm_email(e: str) -> str:
    return (e or "").strip().lower()


def plan_family(name: str) -> str:
    """Bucket a plan name into a family so a link-conversion onto a sibling plan
    (e.g. 'Concierge' vs 'Concierge 2026') still counts as the same conversion."""
    n = (name or "").lower()
    if "concierge" in n or "direct primary" in n or n.strip() == "dpc" or "dpc " in n:
        return "concierge"
    if "glp" in n or "semaglutide" in n or "tirzepatide" in n:
        return "glp"
    if "ketamine" in n:
        return "ketamine"
    if "trt" in n or "testosterone" in n:
        return "trt"
    if "hrt" in n or "hormone" in n or "gaht" in n:
        return "hormone"
    return n.strip() or "unknown"


# --- Hint API: read ----------------------------------------------------------
def _hint_headers():
    return {"Authorization": f"Bearer {HINT_API_KEY}"}


def hint_list_pending_memberships() -> list:
    out = []
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/memberships",
                     headers=_hint_headers(), params={"status": "pending"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        mems = data if isinstance(data, list) else data.get("data", [])
        for m in mems:
            if m.get("status") != "pending":
                continue
            pat_id, name = _patient_of_membership(m)
            plan_id = (m.get("plan") or {}).get("id")
            if plan_id in NURTURE_EXCLUDE_PLANS:
                continue  # Friends & Family $0 comp - never nurture or auto-cancel
            out.append({
                "mem_id": m.get("id"), "pat_id": pat_id, "patient_name": name,
                "plan": (m.get("plan") or {}).get("name"),
                "plan_id": plan_id,
                "start_date": m.get("start_date"), "created_at": m.get("created_at"),
                "status": m.get("status"),
            })
    except Exception as e:
        log.error(f"hint_list_pending_memberships failed: {e}")
    return out


def _patient_of_membership(m: dict):
    mps = m.get("membership_patients") or []
    if mps and isinstance(mps[0], dict):
        pt = mps[0].get("patient") or {}
        return pt.get("id"), pt.get("name")
    return m.get("patient_id"), None


def hint_get_membership(mem_id: str) -> dict | None:
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/memberships/{mem_id}",
                     headers=_hint_headers(), timeout=20)
        if r.status_code == 200:
            d = r.json()
            return d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
        if r.status_code in (404, 410):
            return None
        return None
    except Exception as e:
        log.warning(f"hint_get_membership {mem_id} failed: {e}")
        return None


def hint_payment_methods(pat_id: str):
    """Return list of payment methods, or None on error."""
    if not pat_id:
        return None
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients/{pat_id}/payment_methods",
                     headers=_hint_headers(), timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        return d if isinstance(d, list) else d.get("data", d)
    except Exception as e:
        log.warning(f"hint_payment_methods {pat_id} failed: {e}")
        return None


def hint_has_payment_source(pat_id: str):
    pm = hint_payment_methods(pat_id)
    if pm is None:
        return None
    return len(pm) > 0


def hint_get_patient(pat_id: str) -> dict | None:
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients/{pat_id}",
                     headers=_hint_headers(), timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log.warning(f"hint_get_patient {pat_id} failed: {e}")
        return None


def hint_all_patients() -> list:
    """All patients (single call; ?limit covers the practice; x-total-count
    validated). Used for cross-record duplicate matching."""
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients",
                     headers=_hint_headers(), params={"limit": 1000}, timeout=40)
        if r.status_code != 200:
            log.warning(f"hint_all_patients -> {r.status_code}")
            return []
        d = r.json()
        pts = d if isinstance(d, list) else d.get("data", [])
        total = r.headers.get("x-total-count")
        if total and int(total) > len(pts):
            log.warning(f"hint_all_patients: got {len(pts)} of {total} - pagination needed")
        return pts
    except Exception as e:
        log.warning(f"hint_all_patients failed: {e}")
        return []


def patient_emails_phones(pt: dict):
    emails = {norm_email(pt.get("email"))} if pt.get("email") else set()
    phones = set()
    for p in (pt.get("phones") or []):
        if isinstance(p, dict) and p.get("number"):
            phones.add(normalize_phone_e164(p["number"]))
    return {e for e in emails if e}, {p for p in phones if p}


def first_name_and_phone(pt: dict) -> tuple[str, str]:
    first = (pt.get("chosen_first_name") or "").strip() or (pt.get("first_name") or "").strip()
    phone = ""
    for p in (pt.get("phones") or []):
        if isinstance(p, dict) and ("mobile" in (p.get("type") or "").lower() or "cell" in (p.get("type") or "").lower()):
            phone = (p.get("number") or "").strip()
            if phone:
                break
    if not phone:
        for p in (pt.get("phones") or []):
            if isinstance(p, dict) and (p.get("number") or "").strip():
                phone = p["number"].strip()
                break
    return first, phone


def match_records(all_patients: list, emails: set, phones: set) -> list:
    """All patient records whose email or normalized phone intersects the
    prospect's. Catches link-created DUPLICATE records."""
    out = []
    for pt in all_patients:
        pe, pp = patient_emails_phones(pt)
        if (emails & pe) or (phones & pp):
            out.append(pt)
    return out


def record_membership_signals(pt: dict, our_family: str):
    """Inspect a patient record's embedded memberships for conversion signals.
    Returns (has_active, has_nonpending_same_family)."""
    has_active = False
    nonpending_same_family = False
    for m in (pt.get("memberships") or []):
        st = (m.get("status") or "").lower()
        fam = plan_family((m.get("plan") or {}).get("name"))
        if st == "active":
            has_active = True
        if st and st != "pending" and fam == our_family:
            nonpending_same_family = True
    return has_active, nonpending_same_family


def hint_has_future_appointment(pat_id: str) -> bool | None:
    """True if the patient has a non-canceled appointment starting in the next
    APPT_LOOKAHEAD_DAYS. None on error."""
    if not pat_id:
        return False
    now = datetime.now(timezone.utc)
    start = now.date().isoformat()
    end = (now + timedelta(days=APPT_LOOKAHEAD_DAYS)).date().isoformat()
    try:
        out, offset = [], 0
        while True:
            r = http.get(f"{HINT_BASE_URL}/api/provider/appointments", headers=_hint_headers(),
                         params={"start_date": start, "end_date": end, "limit": 100, "offset": offset}, timeout=30)
            if r.status_code != 200:
                return None
            batch = r.json()
            batch = batch if isinstance(batch, list) else batch.get("data", [])
            out += batch
            if len(batch) < 100:
                break
            offset += 100
        for a in out:
            if (a.get("status") or "").lower() in ("cancelled", "canceled", "declined"):
                continue
            for at in (a.get("attendees") or []):
                pt = at.get("patient") or {}
                if pt.get("id") == pat_id:
                    return True
        return False
    except Exception as e:
        log.warning(f"hint_has_future_appointment {pat_id} failed: {e}")
        return None


# --- Hint API: WRITE (gated by DRY_RUN) --------------------------------------
def hint_cancel_membership(mem_id: str, reason: str, reason_id: str = None) -> bool:
    """Cancel a pending membership: POST /memberships/{id}/cancel with
    end_date = the membership's bill_date (Hint requires end_date to line up with
    bill_date) and cancellation_reason = {"id": <cnr-...>} from the
    cancellation_reasons list (verified live 2026-06-11; "Other" 428s).
    DRY_RUN logs only."""
    fresh = hint_get_membership(mem_id)
    if not fresh:
        log.info(f"cancel skipped: {mem_id} no longer exists")
        return False
    end_date = fresh.get("bill_date") or fresh.get("start_date")
    body = {
        "end_date": end_date,
        "cancellation_reason": {"id": reason_id or CANCEL_REASON_CLEANUP},
    }
    if DRY_RUN:
        log.info(f"[DRY_RUN] would POST cancel {mem_id} end_date={end_date} reason={reason}")
        return True
    try:
        r = http.post(f"{HINT_BASE_URL}/api/provider/memberships/{mem_id}/cancel",
                      headers={**_hint_headers(), "Content-Type": "application/json"},
                      json=body, timeout=25)
        if r.status_code in (200, 201):
            log.info(f"canceled membership {mem_id} (end_date={end_date})")
            return True
        log.error(f"cancel {mem_id} -> {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        log.error(f"cancel {mem_id} failed: {e}")
        return False


# --- Audit trail + staff archive queue ---------------------------------------
def audit(action: str, pat_id: str, mem_id: str, reason: str, guards: dict):
    line = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(), "action": action,
        "patient_id": pat_id, "membership_id": mem_id, "reason": reason,
        "guards": guards, "dry_run": DRY_RUN,
    }, default=str)
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning(f"audit write failed: {e}")
    log.info(f"AUDIT {action}: {line}")


def archive_queue_add(pat_id: str, name: str, mem_id: str, reason: str):
    """Append a patient to the weekly 'staff: archive these' list (Hint API
    cannot archive; archive is a UI action). Never deletes."""
    new = not ARCHIVE_QUEUE_FILE.exists()
    try:
        with open(ARCHIVE_QUEUE_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["queued_at", "patient_id", "name", "membership_id", "reason"])
            if not DRY_RUN:
                w.writerow([datetime.now(timezone.utc).isoformat(), pat_id, name, mem_id, reason])
    except Exception as e:
        log.warning(f"archive_queue write failed: {e}")
    log.info(f"{'[DRY_RUN] would queue' if DRY_RUN else 'queued'} for staff archive: {pat_id} ({name})")


# --- Spruce API: send + thread inspection ------------------------------------
def _spruce_headers():
    return {"Authorization": f"Bearer {SPRUCE_API_KEY}"}


def spruce_list_contacts() -> list:
    out, token = [], None
    while True:
        params = {"pageSize": 200}
        if token:
            params["paginationToken"] = token
        r = http.get(f"{SPRUCE_BASE_URL}/contacts", headers=_spruce_headers(), params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d.get("contacts", [])
        if d.get("hasMore") and d.get("paginationToken"):
            token = d["paginationToken"]
        else:
            break
    return out


def spruce_contact_for_patient(contacts: list, pat_id: str, phone_e164: str) -> dict | None:
    if pat_id:
        for c in contacts:
            for link in (c.get("integrationLinks") or []):
                if link.get("type") == "hint" and link.get("externalId") == pat_id:
                    return c
    if phone_e164:
        for c in contacts:
            for pn in (c.get("phoneNumbers") or []):
                if isinstance(pn, dict) and normalize_phone_e164(pn.get("value") or pn.get("displayValue")) == phone_e164:
                    return c
    return None


def _spruce_inbound_items(contact_id: str):
    items = []
    try:
        rc = http.get(f"{SPRUCE_BASE_URL}/contacts/{contact_id}/conversations", headers=_spruce_headers(), timeout=30)
        if rc.status_code != 200:
            return None
        convs = rc.json().get("conversations") or rc.json().get("data") or []
        for c in convs:
            cid = c.get("id")
            if not cid:
                continue
            ri = http.get(f"{SPRUCE_BASE_URL}/conversations/{cid}/items", headers=_spruce_headers(),
                          params={"pageSize": 200}, timeout=30)
            if ri.status_code != 200:
                continue
            for item in ri.json().get("conversationItems", []):
                if item.get("direction") == "inbound":
                    items.append((item.get("text") or "").strip())
    except Exception as e:
        log.warning(f"inbound items failed for {contact_id}: {e}")
        return None
    return items


def spruce_thread_has_opt_out(contact_id: str) -> bool:
    items = _spruce_inbound_items(contact_id)
    if not items:
        return False
    for txt in items:
        t = txt.lower()
        first = t.split()[0] if t.split() else ""
        if t in OPT_OUT_KEYWORDS or first in OPT_OUT_KEYWORDS:
            return True
    return False


def spruce_thread_has_any_inbound(contact_id: str) -> bool:
    items = _spruce_inbound_items(contact_id)
    return bool(items)


def spruce_send_sms(phone_e164: str, body: str) -> bool:
    if DRY_RUN:
        log.info(f"[DRY_RUN] would send SMS to {_tail(phone_e164)}")
        return True
    if not (SPRUCE_API_KEY and SPRUCE_INTERNAL_ENDPOINT_ID):
        log.warning("Spruce not configured - cannot send.")
        return False
    if not phone_e164:
        return False
    try:
        url = f"{SPRUCE_BASE_URL}/internalendpoints/{SPRUCE_INTERNAL_ENDPOINT_ID}/conversations"
        r = http.post(url, headers={**_spruce_headers(), "Content-Type": "application/json"},
                      json={"destination": {"smsOrEmailEndpoint": phone_e164},
                            "message": {"body": [{"type": "text", "value": body}]}}, timeout=20)
        if r.status_code in (200, 201):
            log.info(f"nurture SMS sent to {_tail(phone_e164)}")
            return True
        log.error(f"Spruce send {r.status_code} to {_tail(phone_e164)}: {r.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Spruce send failed: {e}")
        return False


def _tail(p: str) -> str:
    return f"...{p[-4:]}" if p and len(p) >= 4 else "????"


# --- Nurture state -----------------------------------------------------------
def read_nurture_state() -> dict:
    try:
        if NURTURE_STATE_FILE.exists():
            return json.loads(NURTURE_STATE_FILE.read_text())
    except Exception as e:
        log.warning(f"Could not read nurture_state.json: {e}")
    return {"_meta": {"version": 3, "last_run_at": None, "go_live_at": None}}


def write_nurture_state(state: dict):
    state.setdefault("_meta", {})
    state["_meta"]["version"] = 3
    state["_meta"]["last_run_at"] = datetime.now(timezone.utc).isoformat()
    NURTURE_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


# --- Review-poller de-confliction (READ-ONLY) --------------------------------
def review_asked_today(pat_id: str) -> bool:
    if not pat_id:
        return False
    try:
        if not REVIEW_STATE_FILE.exists():
            return False
        rec = json.loads(REVIEW_STATE_FILE.read_text()).get(pat_id)
        if not rec or not rec.get("last_ask_ts"):
            return False
        last = datetime.fromisoformat(rec["last_ask_ts"].replace("Z", "+00:00"))
        return last.date() == datetime.now(timezone.utc).date()
    except Exception as e:
        log.warning(f"review de-conflict failed for {pat_id}: {e}")
        return False


# --- Helpers -----------------------------------------------------------------
def is_denylisted(name: str) -> bool:
    n = (name or "").lower()
    return any(s in n for s in DENYLIST_NAME_SUBSTRINGS)


def signup_url_for(plan_id: str) -> str | None:
    return PLAN_SIGNUP_URLS.get(plan_id)


def render(day: int, first_name: str, url: str | None = None, plan_id: str | None = None) -> str:
    name = first_name.strip() if first_name else "there"
    if day == 0 and url:
        return DAY0_LINK.format(first_name=name, url=url)
    return texts_for(plan_id)[day].format(first_name=name)


def days_since(iso_dt: str) -> int:
    d = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def days_until(date_str: str) -> int | None:
    """Whole days from today (UTC) until date_str (a 'YYYY-MM-DD' date or ISO
    datetime). Negative if the date is already past. None if empty/unparseable."""
    if not date_str:
        return None
    try:
        s = str(date_str)
        d = (datetime.fromisoformat(s).date() if "T" in s
             else datetime.strptime(s, "%Y-%m-%d").date())
        return (d - datetime.now(timezone.utc).date()).days
    except Exception:
        return None


def due_touch(enrolled_at: str, touches_sent: list, plan_id: str | None = None) -> int | None:
    elapsed = days_since(enrolled_at)
    days = sequence_days_for(plan_id)
    floor = max(touches_sent) if touches_sent else -1   # forward-only: never back-touch
    due = [d for d in days if elapsed >= d and d not in touches_sent and d > floor]
    return max(due) if due else None


def start_date_passed(start_date: str) -> bool:
    if not start_date:
        return False
    try:
        d = (datetime.fromisoformat(str(start_date)).date() if "T" in str(start_date)
             else datetime.strptime(str(start_date), "%Y-%m-%d").date())
        return d < datetime.now(timezone.utc).date()
    except Exception:
        return False
