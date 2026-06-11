#!/usr/bin/env python3
"""
nurture_engine.py - Shared logic for Mt. Baker Medical's post-consult nurture
sequence (T5b/T5c/T5d). T5d: per-plan Day-0 links; cross-record duplicate
suppression + reconcile; Day-30 cleanup (cancel membership + staff archive list;
NEVER delete); audit. All Hint WRITES gated by DRY_RUN. Authoritative backup of
the local file the MBM-Nurture-Poller scheduler runs.
"""
import os, csv, json, logging, sys
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

SCRIPT_DIR = Path(__file__).parent
SPRUCE_API_KEY = os.environ.get("SPRUCE_API_KEY", "")
SPRUCE_INTERNAL_ENDPOINT_ID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
SPRUCE_BASE_URL = "https://api.sprucehealth.com/v1"
HINT_ENV = os.environ.get("HINT_ENV", "production")
HINT_API_KEY = os.environ.get("HINT_API_KEY", "")
HINT_BASE_URL = "https://api.hint.com" if HINT_ENV == "production" else "https://api.sandbox.hint.com"
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

NURTURE_STATE_FILE = SCRIPT_DIR / "nurture_state.json"
REVIEW_STATE_FILE = SCRIPT_DIR / "patient_state.json"
AUDIT_FILE = SCRIPT_DIR / "nurture_audit.log"
ARCHIVE_QUEUE_FILE = SCRIPT_DIR / "nurture_archive_queue.csv"
SEQUENCE_DAYS = [0, 7, 21]
CLEANUP_DAY = 30
APPT_LOOKAHEAD_DAYS = 45
OFFICE_LINE = "(360) 498-7529"
# Hint cancellation_reason ids (GET /api/provider/cancellation_reasons; verified
# live 2026-06-11). cancellation_reason must be {"id": <cnr-...>} - no "Other".
CANCEL_REASON_RECONCILE = "cnr-XDiXnv3xSgom"  # Switched plans
CANCEL_REASON_CLEANUP = "cnr-nQZGCIlgkXEl"    # Contract expired
# Per-plan signup URLs (verified live 2026-06-11). Ketamine (pln-dByXpvwlpFyg) is
# intentionally excluded: no controlled-substance agreement on signup + attorney-
# gated wording (D-006). Unmapped plans fall back to the link-free copy.
PLAN_SIGNUP_URLS = {
    "pln-xjukKCU9Xf6M": "https://mtbakermedical.hint.com/signup/concierge",
    "pln-mjB9MEZD5bio": "https://mtbakermedical.hint.com/signup/so-glp-1-semaglutide",
    "pln-V8YNuahExamp": "https://mtbakermedical.hint.com/signup/so-glp-1-tirzepatide",
    "pln-vVp3WOwlYuyO": "https://mtbakermedical.hint.com/signup/so-hormone-focused-care-hrt",
    "pln-uj91OwP5xH4D": "https://mtbakermedical.hint.com/signup/so-trt-weekly-injection",
    "pln-SVPYWPV612po": "https://mtbakermedical.hint.com/signup/so-trt-topical-cream",
}

DAY0_LINK = ("Hi {first_name}, this is James from Mt. Baker Medical. I really enjoyed "
             "meeting you today. When you're ready to get started, here's your signup "
             "link: {url}. If any questions come up, just reply and it comes straight to "
             "me. Talk soon.")
NURTURE_TEXTS_LINKFREE = {
    0: ("Hi {first_name}, this is James from Mt. Baker Medical. I really enjoyed "
        "meeting you today. Whenever you're ready to get started, just reply here "
        f"or call the office at {OFFICE_LINE} and we'll send your enrollment "
        "paperwork. Any questions, reply anytime, it comes straight to me. Talk soon."),
    7: ("Hi {first_name}, James here, just checking in. Any questions about how "
        "things work, or whether it's the right fit for you? Reply anytime, I'm "
        "glad to help. Whenever you're ready, just reply or call us and we'll get "
        "your enrollment started."),
    21: ("Hi {first_name}, James again. I'll keep the door open whenever you're "
         "ready, no pressure at all. Just reply whenever you'd like to get started. "
         "It was a real pleasure meeting you. Reply STOP to opt out."),
}
DENYLIST_NAME_SUBSTRINGS = ["zz-test", "zztest", "nurtureqa", "nurturecheck", "donotcontact", "do-not-contact"]
OPT_OUT_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "opt-out", "revoke", "remove"}

LOG_FILE = SCRIPT_DIR / "nurture_engine.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE)])
log = logging.getLogger("nurture_engine")


def normalize_phone_e164(phone):
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


def norm_email(e):
    return (e or "").strip().lower()


def plan_family(name):
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


def _hint_headers():
    return {"Authorization": f"Bearer {HINT_API_KEY}"}


def hint_list_pending_memberships():
    out = []
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/memberships", headers=_hint_headers(),
                     params={"status": "pending"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        mems = data if isinstance(data, list) else data.get("data", [])
        for m in mems:
            if m.get("status") != "pending":
                continue
            pat_id, name = _patient_of_membership(m)
            out.append({"mem_id": m.get("id"), "pat_id": pat_id, "patient_name": name,
                        "plan": (m.get("plan") or {}).get("name"), "plan_id": (m.get("plan") or {}).get("id"),
                        "start_date": m.get("start_date"), "created_at": m.get("created_at"), "status": m.get("status")})
    except Exception as e:
        log.error(f"hint_list_pending_memberships failed: {e}")
    return out


def _patient_of_membership(m):
    mps = m.get("membership_patients") or []
    if mps and isinstance(mps[0], dict):
        pt = mps[0].get("patient") or {}
        return pt.get("id"), pt.get("name")
    return m.get("patient_id"), None


def hint_get_membership(mem_id):
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/memberships/{mem_id}", headers=_hint_headers(), timeout=20)
        if r.status_code == 200:
            d = r.json()
            return d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
        return None
    except Exception as e:
        log.warning(f"hint_get_membership {mem_id} failed: {e}")
        return None


def hint_payment_methods(pat_id):
    if not pat_id:
        return None
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients/{pat_id}/payment_methods", headers=_hint_headers(), timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        return d if isinstance(d, list) else d.get("data", d)
    except Exception as e:
        log.warning(f"hint_payment_methods {pat_id} failed: {e}")
        return None


def hint_has_payment_source(pat_id):
    pm = hint_payment_methods(pat_id)
    if pm is None:
        return None
    return len(pm) > 0


def hint_get_patient(pat_id):
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients/{pat_id}", headers=_hint_headers(), timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log.warning(f"hint_get_patient {pat_id} failed: {e}")
        return None


def hint_all_patients():
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients", headers=_hint_headers(), params={"limit": 1000}, timeout=40)
        if r.status_code != 200:
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


def patient_emails_phones(pt):
    emails = {norm_email(pt.get("email"))} if pt.get("email") else set()
    phones = set()
    for p in (pt.get("phones") or []):
        if isinstance(p, dict) and p.get("number"):
            phones.add(normalize_phone_e164(p["number"]))
    return {e for e in emails if e}, {p for p in phones if p}


def first_name_and_phone(pt):
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


def match_records(all_patients, emails, phones):
    out = []
    for pt in all_patients:
        pe, pp = patient_emails_phones(pt)
        if (emails & pe) or (phones & pp):
            out.append(pt)
    return out


def record_membership_signals(pt, our_family):
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


def hint_has_future_appointment(pat_id):
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
                if (at.get("patient") or {}).get("id") == pat_id:
                    return True
        return False
    except Exception as e:
        log.warning(f"hint_has_future_appointment {pat_id} failed: {e}")
        return None


def hint_cancel_membership(mem_id, reason, reason_id=None):
    fresh = hint_get_membership(mem_id)
    if not fresh:
        return False
    end_date = fresh.get("bill_date") or fresh.get("start_date")
    body = {"end_date": end_date, "cancellation_reason": {"id": reason_id or CANCEL_REASON_CLEANUP}}
    if DRY_RUN:
        log.info(f"[DRY_RUN] would POST cancel {mem_id} end_date={end_date} reason={reason}")
        return True
    try:
        r = http.post(f"{HINT_BASE_URL}/api/provider/memberships/{mem_id}/cancel",
                      headers={**_hint_headers(), "Content-Type": "application/json"}, json=body, timeout=25)
        if r.status_code in (200, 201):
            log.info(f"canceled membership {mem_id} (end_date={end_date})")
            return True
        log.error(f"cancel {mem_id} -> {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        log.error(f"cancel {mem_id} failed: {e}")
        return False


def audit(action, pat_id, mem_id, reason, guards):
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "action": action, "patient_id": pat_id,
                       "membership_id": mem_id, "reason": reason, "guards": guards, "dry_run": DRY_RUN}, default=str)
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning(f"audit write failed: {e}")
    log.info(f"AUDIT {action}: {line}")


def archive_queue_add(pat_id, name, mem_id, reason):
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


def _spruce_headers():
    return {"Authorization": f"Bearer {SPRUCE_API_KEY}"}


def spruce_list_contacts():
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


def spruce_contact_for_patient(contacts, pat_id, phone_e164):
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


def _spruce_inbound_items(contact_id):
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
        log.warning(f"inbound items failed: {e}")
        return None
    return items


def spruce_thread_has_opt_out(contact_id):
    items = _spruce_inbound_items(contact_id)
    if not items:
        return False
    for txt in items:
        t = txt.lower()
        first = t.split()[0] if t.split() else ""
        if t in OPT_OUT_KEYWORDS or first in OPT_OUT_KEYWORDS:
            return True
    return False


def spruce_thread_has_any_inbound(contact_id):
    items = _spruce_inbound_items(contact_id)
    return bool(items)


def spruce_send_sms(phone_e164, body):
    if DRY_RUN:
        log.info(f"[DRY_RUN] would send SMS to {_tail(phone_e164)}")
        return True
    if not (SPRUCE_API_KEY and SPRUCE_INTERNAL_ENDPOINT_ID) or not phone_e164:
        return False
    try:
        url = f"{SPRUCE_BASE_URL}/internalendpoints/{SPRUCE_INTERNAL_ENDPOINT_ID}/conversations"
        r = http.post(url, headers={**_spruce_headers(), "Content-Type": "application/json"},
                      json={"destination": {"smsOrEmailEndpoint": phone_e164},
                            "message": {"body": [{"type": "text", "value": body}]}}, timeout=20)
        return r.status_code in (200, 201)
    except Exception as e:
        log.error(f"send failed: {e}")
        return False


def _tail(p):
    return f"...{p[-4:]}" if p and len(p) >= 4 else "????"


def read_nurture_state():
    try:
        if NURTURE_STATE_FILE.exists():
            return json.loads(NURTURE_STATE_FILE.read_text())
    except Exception as e:
        log.warning(f"read state failed: {e}")
    return {"_meta": {"version": 3, "last_run_at": None, "go_live_at": None}}


def write_nurture_state(state):
    state.setdefault("_meta", {})
    state["_meta"]["version"] = 3
    state["_meta"]["last_run_at"] = datetime.now(timezone.utc).isoformat()
    NURTURE_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def review_asked_today(pat_id):
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
    except Exception:
        return False


def is_denylisted(name):
    n = (name or "").lower()
    return any(s in n for s in DENYLIST_NAME_SUBSTRINGS)


def signup_url_for(plan_id):
    return PLAN_SIGNUP_URLS.get(plan_id)


def render(day, first_name, url=None):
    name = first_name.strip() if first_name else "there"
    if day == 0 and url:
        return DAY0_LINK.format(first_name=name, url=url)
    return NURTURE_TEXTS_LINKFREE[day].format(first_name=name)


def days_since(iso_dt):
    d = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def due_touch(enrolled_at, touches_sent):
    elapsed = days_since(enrolled_at)
    due = [d for d in SEQUENCE_DAYS if elapsed >= d and d not in touches_sent]
    return max(due) if due else None


def start_date_passed(start_date):
    if not start_date:
        return False
    try:
        d = (datetime.fromisoformat(str(start_date)).date() if "T" in str(start_date)
             else datetime.strptime(str(start_date), "%Y-%m-%d").date())
        return d < datetime.now(timezone.utc).date()
    except Exception:
        return False
