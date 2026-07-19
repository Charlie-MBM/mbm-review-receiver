#!/usr/bin/env python3
"""
export_dashboard_members.py - READ-ONLY Hint -> dashboard feed.

Pulls this-month's NEW memberships (Hint "Starting this month", membership-owner
semantics) and each member's self-reported Lead Source via the Hint API, tallies
AGGREGATE COUNTS ONLY (no names, no PHI), and writes a small JSON the MBM weekly
dashboard refresh reads in place of the old browser scrape.

NEW-MEMBER DEFINITION (2026-07 update): a membership counts as a real new member only if
(1) it was CREATED this month (created_at in the month = actually signed up this month --
NOT start_date, which is Hint's billing anchor and also fires for renewals, reactivations,
plan changes and future-dated starts, which inflated the count), AND (2) it has a payment
method on file (GET /patients/{id}/payment_methods, same signal the nurture engine uses).
A membership with no payment method is "pending" and reported separately in `members_pending`
- it is NOT counted in `members`. For transparency the OLD start_date-anchored count is still
exported as `members_anchored_by_start_date` so the renewals-counted-as-new gap is visible,
and `new_member_basis` records which date field was used. If created_at isn't exposed by the
API we fall back to start_date and flag it; if the payment endpoint can't be reached for ANY
member in a run, we fall back to counting all memberships (old behavior) and flag it in
`warnings` rather than zeroing the dashboard.

CONSULT DEFINITION (2026-07 fix): a consult is a booking whose non-staff attendee is still a
Contact (no patient id) = a real prospect -- the same rule consult_count.py/send_daily_summary
already use in prod. The OLD rule (duration == 30 min OR 'consult' in the title) assumed the
free consult was the practice's only 30-minute slot; it isn't (follow-ups and member visits
share it), so July over-reported 14 when the true booked count was 7. Because Hint DELETES the
Contact attendee once a prospect enrols, counting retroactively over a month window under-reports
converted consults -- so the authoritative "booked this month" number is the never-decaying tally
consult_count.py persists each daily run, surfaced here as consults.booked_mtd_running_tally
(and flagged if that state file has gone stale). The old count ships as `consults_legacy_30min`
purely so the gap stays visible.

GUARDRAILS (mirrors nurture_engine's philosophy):
  * READ-ONLY. Only HTTP GETs to Hint. No writes, no Spruce, no SMS, no DRY_RUN
    needed. Does NOT import or touch the review/nurture senders or their state.
  * Output JSON contains ONLY aggregate counts + diagnostics. Never a patient name.
  * Reuses the existing .env (HINT_API_KEY) - the key never leaves this laptop.

USAGE:
  py export_dashboard_members.py                 # write feed JSON for the current month
  py export_dashboard_members.py --month 2026-06 # a specific month
  py export_dashboard_members.py --probe         # ALSO print which patient field holds
                                                 # Lead Source + the raw values seen
                                                 # (run this once to confirm the API
                                                 #  exposes lead source)
  py export_dashboard_members.py --out PATH      # override output path

The weekly dashboard task reads the file at FEED_OUT (default: the dashboard
artifact's own folder, so the cloud task can read it as a sibling of index.html).
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, date
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

# --- Config (reuses the review/nurture poller's .env) ------------------------
HINT_ENV = os.environ.get("HINT_ENV", "production")
HINT_API_KEY = os.environ.get("HINT_API_KEY", "")
HINT_BASE_URL = (
    "https://api.hint.com" if HINT_ENV == "production"
    else "https://api.sandbox.hint.com"
)

# Default output: sibling of the dashboard artifact (the weekly task already reads
# this folder via list_artifacts, so a file dropped here is trivially readable).
DEFAULT_OUT = Path(os.environ.get(
    "FEED_OUT",
    r"C:\Users\charl\OneDrive\Documents\Claude\Artifacts\mbm-performance-dashboard\members_feed.json",
))

# The owner's own test account + obvious test records to exclude (by patient name).
EXCLUDE_NAME_SUBSTRINGS = ["charles robert platt", "zztest", "zz-test", "test", "nurtureqa", "donotcontact"]

# Membership PLAN types that are NOT real new members and must be excluded from the
# member count (comp / non-acquisition memberships). Matched case-insensitively as a
# substring of the Hint plan name. "friend" covers "Friends and Family" / "Friends & Family".
# Charlie: confirm the exact plan name in Hint and adjust this list if it differs.
EXCLUDE_PLAN_SUBSTRINGS = ["friends and family", "friends & family", "friend", "f&f"]

# The 7 dashboard lead-source buckets (must match the dashboard + Hint dropdown).
SOURCE_KEYS = ["google", "google_lsa", "bing", "ai", "social", "provider_referral", "word_of_mouth", "other"]

# LSA kill/keep test window (dashboard "LSA test" tile; DASHBOARD_METRICS.md §1).
# Patients attributed = members/SO with Hint Lead Source mapping to google_lsa,
# signed up ON OR AFTER this date. Cumulative across months — not MTD.
LSA_TEST_SINCE = "2026-07-17"


def _headers():
    return {"Authorization": f"Bearer {HINT_API_KEY}"}


def _get(path, params=None):
    r = http.get(f"{HINT_BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=40)
    r.raise_for_status()
    return r


def _as_list(payload):
    return payload if isinstance(payload, list) else (payload.get("data", []) if isinstance(payload, dict) else [])


# --- Hint reads --------------------------------------------------------------
def list_memberships():
    """All memberships, paginated. Membership-owner semantics (one object per
    membership), so family memberships aren't double-counted."""
    out, offset = [], 0
    while True:
        r = _get("/api/provider/memberships", {"limit": 100, "offset": offset})
        batch = _as_list(r.json())
        out += batch
        total = r.headers.get("x-total-count")
        if len(batch) < 100 or (total and len(out) >= int(total)):
            break
        offset += 100
        if offset > 5000:  # safety
            break
    return out


def get_patient(pat_id):
    try:
        r = _get(f"/api/provider/patients/{pat_id}")
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def get_payment_methods(pat_id):
    """Return list (possibly empty) of payment methods on file, or None on error/unavailable.
    Mirrors nurture_engine.hint_payment_methods (proven in prod since 2026-06)."""
    if not pat_id:
        return None
    try:
        r = http.get(f"{HINT_BASE_URL}/api/provider/patients/{pat_id}/payment_methods",
                     headers=_headers(), timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        return d if isinstance(d, list) else d.get("data", d)
    except Exception:
        return None


def has_payment_source(pat_id):
    """True = >=1 payment method on file; False = none on file; None = couldn't verify."""
    pm = get_payment_methods(pat_id)
    if pm is None:
        return None
    return len(pm) > 0


def list_appointments(start_iso, end_iso):
    out, offset = [], 0
    while True:
        try:
            r = _get("/api/provider/appointments",
                     {"start_date": start_iso, "end_date": end_iso, "limit": 100, "offset": offset})
        except Exception:
            return None
        batch = _as_list(r.json())
        out += batch
        if len(batch) < 100:
            break
        offset += 100
        if offset > 5000:
            break
    return out


# --- Field extraction --------------------------------------------------------
def patient_name_of_membership(m):
    mps = m.get("membership_patients") or []
    if mps and isinstance(mps[0], dict):
        pt = mps[0].get("patient") or {}
        return pt.get("id"), (pt.get("name") or "")
    return m.get("patient_id"), ""


def plan_name_of(m):
    return ((m.get("plan") or {}).get("name")) or ""


def bucket_plan(name):
    """concierge vs so (standing-order / weight-loss / hormone), matching the
    dashboard. Ambiguous -> concierge (and flagged by caller)."""
    n = (name or "").lower()
    if "concierge" in n or "membership" in n or "direct primary" in n or n.strip() == "dpc" or "dpc " in n:
        return "concierge"
    if (n.startswith("so") or "glp" in n or "semaglutide" in n or "tirzepatide" in n
            or "trt" in n or "testosterone" in n or "hrt" in n or "hormone" in n
            or "fitness glp" in n or "standing order" in n):
        return "so"
    return "concierge"  # ambiguous default


def is_friends_family(plan_name):
    """True if the plan is a comp / friends-and-family type that should NOT count as a
    real new member. Matched as a case-insensitive substring of the plan name."""
    n = (plan_name or "").lower()
    return any(sub in n for sub in EXCLUDE_PLAN_SUBSTRINGS)


def extract_lead_source(pt):
    """Return (raw_value, found_key) for the patient's Lead Source.

    Confirmed 2026-06-11 via --probe: Hint exposes it as the top-level string
    field `lead_source`. We read that strictly (string, or an object with
    name/value) and only a couple of exact alternates — NO loose/substring
    scanning, which previously false-matched `preferred_language` when
    lead_source was empty. Empty/missing -> (None, None) -> bucketed 'other'.
    """
    if not isinstance(pt, dict):
        return None, None
    v = pt.get("lead_source")
    if isinstance(v, str) and v.strip():
        return v.strip(), "lead_source"
    if isinstance(v, dict):
        nm = v.get("name") or v.get("value") or v.get("label")
        if nm and str(nm).strip():
            return str(nm).strip(), "lead_source"
    for k in ("leadSource", "referral_source"):
        vv = pt.get(k)
        if isinstance(vv, str) and vv.strip():
            return vv.strip(), k
    return None, None


def map_source(raw):
    """Map a raw Hint Lead Source value to one of the 7 dashboard buckets."""
    if not raw:
        return "other"
    r = raw.strip().lower()
    # LSA must be checked BEFORE the google catch-all: "Google Local Services"
    # contains "google" and would otherwise vanish into the generic bucket,
    # making the LSA test tile's patients_attributed permanently zero.
    if "local service" in r or "lsa" in r.replace("/", " ").replace("-", " ").replace("(", " ").replace(")", " ").split():
        return "google_lsa"
    if "bing" in r:
        return "bing"
    if "google" in r or "search" in r or "online" in r:
        return "google"
    if r == "ai" or "chatgpt" in r or "perplexity" in r or "artificial intel" in r or r.startswith("ai ") or r.endswith(" ai"):
        return "ai"
    if "social" in r or "facebook" in r or "instagram" in r or "meta" in r or "tiktok" in r or "youtube" in r:
        return "social"
    if "provider" in r or "referr" in r or "emergency" in r or " er" in f" {r}" or "doctor" in r or "physician" in r or "hospital" in r or "clinic" in r:
        return "provider_referral"
    if "word of mouth" in r or "friend" in r or "family" in r or "patient" in r or "spouse" in r:
        return "word_of_mouth"
    return "other"


# Single source of truth for "is this booking a real prospect consult?" -- reuse the rule
# consult_count.py / send_daily_summary.py already run in prod rather than re-deriving it.
try:
    from consult_count import is_consult_booking as _is_consult_booking
except Exception:  # pragma: no cover - consult_count sits next to this file
    def _is_consult_booking(a):
        for att in (a.get("attendees") or []):
            if (att.get("type") or "").lower() == "staff":
                continue
            return ((att.get("patient") or {}) or {}).get("id") is None
        return False

CONSULT_STATE_FILE = Path(__file__).parent / "consult_count_state.json"


def read_consult_tally(period):
    """The erasure-proof monthly consult tally maintained by consult_count.py on each daily
    summary run. Returns None if missing or for a different month. Also reports how stale the
    state file is, since the tally only advances when the daily poller actually runs."""
    try:
        st = json.loads(CONSULT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if st.get("month") != period:
        return None
    written, stale_days = None, None
    try:
        mtime = datetime.fromtimestamp(CONSULT_STATE_FILE.stat().st_mtime, timezone.utc)
        written = mtime.isoformat()
        stale_days = (datetime.now(timezone.utc) - mtime).days
    except Exception:
        pass
    return {"mtd_count": int(st.get("mtd_count") or 0),
            "state_written": written, "stale_days": stale_days}


CREATED_KEYS = ("created_at", "created", "enrolled_at", "signed_up_at", "createdAt", "created_on")


def created_at_of(m):
    """Membership signup timestamp (when the membership record was created in Hint), as
    opposed to start_date (the billing anchor, which also moves on renewals / reactivations
    / plan changes). Returns the first present of CREATED_KEYS, or None if none are exposed."""
    if not isinstance(m, dict):
        return None
    for k in CREATED_KEYS:
        v = m.get(k)
        if v:
            return v
    return None


def in_month(start_date_str, y, mo):
    if not start_date_str:
        return False
    s = str(start_date_str)
    try:
        d = (datetime.fromisoformat(s).date() if "T" in s else datetime.strptime(s[:10], "%Y-%m-%d").date())
        return d.year == y and d.month == mo
    except Exception:
        return False


def is_excluded(name):
    n = (name or "").lower()
    return any(sub in n for sub in EXCLUDE_NAME_SUBSTRINGS)


# --- Main --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM (default: current month, UTC)")
    ap.add_argument("--out", help="output JSON path")
    ap.add_argument("--probe", action="store_true", help="print lead-source field + raw values seen")
    args = ap.parse_args()

    if not HINT_API_KEY:
        print("ERROR: HINT_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(2)

    now = datetime.now(timezone.utc)
    if args.month:
        y, mo = int(args.month[:4]), int(args.month[5:7])
    else:
        y, mo = now.year, now.month
    period = f"{y:04d}-{mo:02d}"
    month_start = date(y, mo, 1).isoformat()
    month_end = (date(y + (mo == 12), (mo % 12) + 1, 1)).isoformat()  # first of next month (exclusive-ish)
    out_path = Path(args.out) if args.out else DEFAULT_OUT

    warnings = []
    # A real new member = membership that started this month AND has a payment method
    # on file. Membership but no payment = "pending". Mirrors the nurture engine's
    # "assigned but hasn't paid" signal (GET /patients/{id}/payment_methods).
    paid_members = {"concierge": 0, "so": 0, "total": 0}   # counted as new members (signed up this month)
    paid_source = {k: 0 for k in SOURCE_KEYS}
    anchored_paid = {"concierge": 0, "so": 0, "total": 0}  # OLD basis: start_date this month, paid (comparison only)
    pending = {"concierge": 0, "so": 0, "total": 0}        # membership, NO payment on file
    payment_unknown = 0                                    # payment status couldn't be verified (API error)
    # Ungated tallies over every started membership — used only for the systemic-failure fallback.
    all_members = {"concierge": 0, "so": 0, "total": 0}
    all_source = {k: 0 for k in SOURCE_KEYS}
    pending_future_dated = 0
    excluded_count = 0
    friends_family_excluded = 0   # comp / friends-and-family memberships, NOT counted as new members
    ambiguous_plans = []
    lead_source_field = None
    lead_source_unmapped = {}   # raw value -> count, for anything that landed in "other"
    probe_rows = []

    try:
        all_mems = list_memberships()
    except Exception as e:
        print(f"ERROR: could not list memberships: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Active member totals (2026-07-16: the dashboard North Star = active Concierge
    # members vs the 300 goal). Counts ALL currently-active memberships (not just this
    # month's signups), bucketed concierge/so. Status-based — Hint's billing truth for
    # existing members; no per-patient payment calls (too heavy across the whole book).
    # Test accounts and comp/F&F plans are excluded from the headline but reported
    # alongside. Defensive: a failure here must never break the rest of the feed.
    active_members = None
    try:
        _act = {"concierge": 0, "so": 0, "total": 0}
        _act_ff = 0
        _act_name_excluded = 0
        _status_histogram = {}
        # Aggregate reconciliation grid: status × bucket, with excluded memberships
        # tracked per status too — so a "Hint badge says 17, dashboard says 14" gap
        # is answerable from the feed alone (no names exported, counts only).
        _recon = {}
        def _recon_add(st, key):
            _recon.setdefault(st, {})[key] = _recon.get(st, {}).get(key, 0) + 1
        for _m in all_mems:
            _st = (_m.get("status") or "unknown").lower()
            _status_histogram[_st] = _status_histogram.get(_st, 0) + 1
            _pid, _nm = patient_name_of_membership(_m)
            _plan = plan_name_of(_m)
            if is_excluded(_nm):
                _recon_add(_st, "test_account_excluded")
                if _st == "active":
                    _act_name_excluded += 1
                continue
            if is_friends_family(_plan):
                _recon_add(_st, "friends_family")
                if _st == "active":
                    _act_ff += 1
                continue
            _b = bucket_plan(_plan)
            _recon_add(_st, _b)
            if _st != "active":
                continue
            _act[_b] += 1
            _act["total"] += 1
        active_members = dict(_act)
        active_members["friends_family_active_excluded"] = _act_ff
        active_members["test_accounts_active_excluded"] = _act_name_excluded
        active_members["status_histogram"] = _status_histogram
        active_members["reconciliation_by_status"] = _recon
        active_members["basis"] = "membership status == 'active'; comps/F&F and test accounts excluded"
        if _act["total"] == 0:
            warnings.append("active_members counted 0 with status=='active' — Hint may use a different "
                            "status value for live memberships; see active_members.status_histogram "
                            "and adjust the filter.")
    except Exception as _e:
        warnings.append(f"active_members tally failed ({_e}) — dashboard North Star falls back to "
                        f"manual entry until fixed.")

    # --- Terminations this month (feeds net member growth = adds − terms).
    # A TERMINATION means a PAYING member left. The nurture pipeline CANCELS stale unpaid
    # pending memberships (day-30 cleanup / duplicate reconcile — see nurture_engine.py,
    # CANCEL_REASON_CLEANUP + CANCEL_REASON_RECONCILE); those cancels carry end dates but
    # are NOT churn (the person was never a paying member) and MUST be excluded (2026-07-17,
    # Charlie's catch: mem-R6kubnHgfC4B reconcile-canceled 7/14 with end_date 7/20 would
    # otherwise have counted as a July termination). Two gates:
    #   1. cancellation_reason id in NURTURE_CANCEL_REASON_IDS -> nurture cleanup, excluded.
    #   2. otherwise, the patient must have a payment method on file (same signal as the
    #      new-member paid gate) -> no payment = never-paid pending, excluded; unverifiable
    #      = counted separately as payment_unverified, never silently included.
    # Defensive: never breaks the feed; exports its own diagnostics.
    NURTURE_CANCEL_REASON_IDS = {"cnr-nQZGCIlgkXEl",   # "Contract expired" = day-30 cleanup
                                 "cnr-XDiXnv3xSgom"}   # "Switched plans"   = duplicate reconcile
    terminations = None
    try:
        _END_KEYS = ("end_date", "ended_at", "cancelled_at", "canceled_at",
                     "termination_date", "terminated_at", "cancellation_date")
        _END_STATUSES = ("cancelled", "canceled", "terminated", "ended", "inactive", "expired")
        def _cancel_reason_id(m):
            cr = m.get("cancellation_reason")
            if isinstance(cr, dict):
                return cr.get("id")
            if isinstance(cr, str):
                return cr
            return None
        _t = {"concierge": 0, "so": 0, "total": 0}
        _t_ff = 0
        _nurture_excluded = 0
        _never_paid_excluded = 0
        _pay_unverified = 0
        _end_field = None
        _end_status_no_date = 0
        for _m in all_mems:
            _endraw = None
            for _k in _END_KEYS:
                if _m.get(_k):
                    _endraw = _m.get(_k)
                    _end_field = _end_field or _k
                    break
            _st = (_m.get("status") or "").lower()
            if _endraw is None:
                if any(s in _st for s in _END_STATUSES):
                    _end_status_no_date += 1
                continue
            if not in_month(_endraw, y, mo):
                continue
            _pid2, _nm2 = patient_name_of_membership(_m)
            if is_excluded(_nm2):
                continue
            _plan2 = plan_name_of(_m)
            if is_friends_family(_plan2):
                _t_ff += 1
                continue
            # Gate 1: nurture/reconcile cancels are pipeline hygiene, not churn.
            if _cancel_reason_id(_m) in NURTURE_CANCEL_REASON_IDS:
                _nurture_excluded += 1
                continue
            # Gate 2: only ever-paying members count as terminations.
            _pay2 = has_payment_source(_pid2) if _pid2 else None
            if _pay2 is False:
                _never_paid_excluded += 1
                continue
            if _pay2 is None:
                _pay_unverified += 1
                continue
            _t[bucket_plan(_plan2)] += 1
            _t["total"] += 1
        terminations = dict(_t)
        terminations["friends_family_excluded"] = _t_ff
        terminations["nurture_cleanup_excluded"] = _nurture_excluded
        terminations["never_paid_excluded"] = _never_paid_excluded
        terminations["payment_unverified"] = _pay_unverified
        terminations["end_field"] = _end_field
        terminations["end_status_without_date"] = _end_status_no_date
        terminations["basis"] = ("membership end-date in month, PAYING members only; nurture/"
                                 "reconcile cancels, never-paid pendings, comps/F&F and test "
                                 "accounts excluded")
        if _pay_unverified > 0:
            warnings.append(f"terminations: {_pay_unverified} ended membership(s) could not be "
                            f"payment-verified and are excluded from the count — verify manually "
                            f"in Hint whether they were paying members.")
        if _end_field is None and _end_status_no_date > 0:
            warnings.append(f"terminations: no end-date field found on membership objects, but "
                            f"{_end_status_no_date} membership(s) carry an end-like status. "
                            f"Termination counts will read 0 until the right field is identified — "
                            f"check membership object keys and extend _END_KEYS.")
    except Exception as _e:
        warnings.append(f"terminations tally failed ({_e}) — net-growth on the dashboard will "
                        f"show adds only until fixed.")

    # --- Signup-date basis --------------------------------------------------
    # A "new member this month" should mean someone who SIGNED UP this month, i.e. the
    # membership's created_at is in the month -- NOT start_date, which is Hint's billing
    # anchor and also fires for renewals / reactivations / plan changes / future-dated
    # starts (that inflated the count). Prefer created_at; if Hint's membership objects
    # don't expose it, fall back to start_date and flag it loudly.
    has_created_field = any(created_at_of(m) is not None for m in all_mems)
    signup_basis_field = "created_at" if has_created_field else "start_date"

    def signup_in_month(m):
        raw = created_at_of(m) if has_created_field else m.get("start_date")
        return in_month(raw, y, mo)

    if not has_created_field:
        warnings.append("Hint membership objects did not expose a created_at/signup field this run - "
                        "fell back to counting new members by start_date (may include renewals / "
                        "reactivations). Check the membership object keys and update CREATED_KEYS if "
                        "the field name differs.")

    # Primary set = signed up this month; also keep start_date-anchored memberships for the
    # side-by-side comparison so the gap (renewals counted as 'new') stays visible.
    relevant = [m for m in all_mems if signup_in_month(m) or in_month(m.get("start_date"), y, mo)]

    for m in relevant:
        pat_id, name = patient_name_of_membership(m)
        if is_excluded(name):
            excluded_count += 1
            continue
        plan = plan_name_of(m)
        if is_friends_family(plan):
            # Comp / friends-and-family membership: real signup, but not an acquisition
            # new member. Exclude from members AND pending; report the count separately.
            friends_family_excluded += 1
            continue
        b = bucket_plan(plan)
        if b == "concierge" and plan and not any(t in plan.lower() for t in ("concierge", "membership", "direct primary", "dpc")):
            ambiguous_plans.append(plan)

        signup_in = signup_in_month(m)
        started_in = in_month(m.get("start_date"), y, mo)

        status = (m.get("status") or "").lower()
        try:
            future = datetime.strptime(str(m.get("start_date"))[:10], "%Y-%m-%d").date() > now.date()
        except Exception:
            future = False
        if signup_in and (status == "pending" or future):
            pending_future_dated += 1

        # Lead source + payment status (read once; used by both bases).
        raw, key = (None, None)
        if pat_id:
            pt = get_patient(pat_id)
            raw, key = extract_lead_source(pt)
            if key and not lead_source_field:
                lead_source_field = key
        bucket = map_source(raw)
        has_pay = has_payment_source(pat_id) if pat_id else None

        # PRIMARY basis = actually signed up this month (created_at, or start_date fallback).
        if signup_in:
            all_members[b] += 1            # ungated tally (used only for the systemic-failure fallback)
            all_members["total"] += 1
            all_source[bucket] += 1
            if raw and bucket == "other":
                lead_source_unmapped[raw] = lead_source_unmapped.get(raw, 0) + 1
            if has_pay is True:
                paid_members[b] += 1
                paid_members["total"] += 1
                paid_source[bucket] += 1
            elif has_pay is False:
                pending[b] += 1
                pending["total"] += 1
            else:
                payment_unknown += 1

        # COMPARISON basis = membership anchored (start_date) this month, old behaviour, paid only.
        if started_in and has_pay is True:
            anchored_paid[b] += 1
            anchored_paid["total"] += 1

        if args.probe:
            probe_rows.append({"plan_bucket": b, "signup_in": signup_in, "started_in": started_in,
                               "lead_source_raw": raw, "field": key,
                               "mapped": bucket, "has_payment": has_pay})

    # --- Revenue MTD (dashboard profitability widget, added 2026-07-18) ---------
    # Sum of payments COLLECTED this month from Hint. The Hint provider API's
    # billing surface isn't documented in our code yet, so this probes candidate
    # endpoints defensively and reports exactly what it used. PHI: aggregate
    # dollars only - no names, no line items exported.
    revenue_mtd = None
    try:
        _rev_endpoints = ["/api/provider/payments", "/api/provider/invoices",
                          "/api/provider/transactions", "/api/provider/charges"]
        _AMT_KEYS = ("amount_cents", "total_cents", "amount", "total", "amount_due_cents", "paid_amount")
        _DATE_KEYS = ("paid_at", "collected_at", "created_at", "date", "charged_at", "posted_at")
        _PAIDISH = ("paid", "succeeded", "settled", "completed", "captured", "collected")
        for _ep in _rev_endpoints:
            try:
                _out, _off = [], 0
                while True:
                    _r = _get(_ep, {"limit": 100, "offset": _off})
                    _batch = _as_list(_r.json())
                    _out += _batch
                    _tot = _r.headers.get("x-total-count")
                    if len(_batch) < 100 or (_tot and len(_out) >= int(_tot)) or _off > 20000:
                        break
                    _off += 100
            except Exception:
                continue
            if not _out:
                continue
            _sum_c, _n, _amt_field, _date_field, _cents = 0, 0, None, None, None
            for _p in _out:
                if not isinstance(_p, dict):
                    continue
                _st = str(_p.get("status") or "").lower()
                if _st and not any(k in _st for k in _PAIDISH):
                    continue
                _draw = None
                for _dk in _DATE_KEYS:
                    if _p.get(_dk):
                        _draw = _p.get(_dk); _date_field = _date_field or _dk; break
                if not _draw or not in_month(_draw, y, mo):
                    continue
                for _ak in _AMT_KEYS:
                    if _p.get(_ak) is not None:
                        try:
                            _v = float(_p.get(_ak))
                        except Exception:
                            continue
                        if _cents is None:
                            _cents = _ak.endswith("_cents") or _v > 100000  # heuristic once
                        _sum_c += _v; _n += 1; _amt_field = _amt_field or _ak
                        break
            if _n > 0:
                _dollars = round(_sum_c / 100.0, 2) if (_amt_field or "").endswith("_cents") else round(_sum_c, 2)
                revenue_mtd = {"collected": _dollars, "payments_count": _n,
                               "endpoint": _ep, "amount_field": _amt_field,
                               "date_field": _date_field,
                               "basis": "payments with paid-like status, dated this month",
                               "cents_assumed": bool((_amt_field or "").endswith("_cents"))}
                if not (_amt_field or "").endswith("_cents"):
                    warnings.append(f"revenue_mtd: amount field '{_amt_field}' has no _cents suffix - "
                                    f"assumed DOLLARS. Verify one payment in Hint against the total "
                                    f"(${_dollars}) before trusting the profitability widget.")
                break
        if revenue_mtd is None:
            warnings.append("revenue_mtd: no billing endpoint yielded payment rows "
                            "(tried payments/invoices/transactions/charges). Profitability "
                            "widget's revenue side stays unwired - check Hint API docs/scopes.")
    except Exception as _e:
        warnings.append(f"revenue_mtd probe failed ({_e}) - revenue stays unwired.")

    # --- LSA test tile: patients attributed to Google Local Services ------------
    # Cumulative since LSA_TEST_SINCE (crosses month boundaries — deliberately NOT
    # scoped to `relevant`). Attributed = signup on/after the test start whose Hint
    # Lead Source maps to google_lsa. Paid and pending are counted separately: the
    # kill/keep rule evaluates on PAID patients only (pending can still be nurture-
    # cancelled at day 30). PHI: aggregate counts only, no names exported.
    lsa_test = {"since": LSA_TEST_SINCE, "patients_attributed": 0,
                "attributed_pending": 0, "attributed_unverified_payment": 0,
                "signups_checked": 0}
    try:
        for m in all_mems:
            _raw_dt = created_at_of(m) if has_created_field else m.get("start_date")
            if not _raw_dt or str(_raw_dt)[:10] < LSA_TEST_SINCE:
                continue
            _pid3, _nm3 = patient_name_of_membership(m)
            if is_excluded(_nm3) or is_friends_family(plan_name_of(m)):
                continue
            lsa_test["signups_checked"] += 1
            if not _pid3:
                continue
            _raw_src, _ = extract_lead_source(get_patient(_pid3) or {})
            if map_source(_raw_src) != "google_lsa":
                continue
            _pay3 = has_payment_source(_pid3)
            if _pay3 is True:
                lsa_test["patients_attributed"] += 1
            elif _pay3 is False:
                lsa_test["attributed_pending"] += 1
            else:
                lsa_test["attributed_unverified_payment"] += 1
        if lsa_test["attributed_unverified_payment"]:
            warnings.append(f"lsa_test: {lsa_test['attributed_unverified_payment']} LSA-attributed "
                            f"signup(s) could not be payment-verified — excluded from "
                            f"patients_attributed; verify manually in Hint.")
    except Exception as _e:
        lsa_test = None
        warnings.append(f"lsa_test tally failed ({_e}) — dashboard LSA test tile keeps last-good "
                        f"attributed count (zero ≠ null: do not bake a fresh 0).")

    # Choose the member basis. If payment status couldn't be verified for ANY member
    # (systemic endpoint/scope failure), don't zero out the dashboard — fall back to the
    # old "count all memberships" behavior and flag it loudly.
    nonexcluded = all_members["total"]
    if nonexcluded > 0 and payment_unknown == nonexcluded:
        members = all_members
        members_source = all_source
        pending = {"concierge": 0, "so": 0, "total": 0}
        members_counted_basis = "all_fallback"
        warnings.append("payment_methods endpoint returned no data for ANY new membership this run - "
                        "could not verify payment on file. Counted ALL new memberships as members "
                        "(old behavior); pending split unavailable. Check API key scope / endpoint.")
    else:
        members = paid_members
        members_source = paid_source
        members_counted_basis = "paid"
        if payment_unknown > 0:
            warnings.append(f"{payment_unknown} new membership(s) could not be payment-verified (API error) "
                            f"and are excluded from BOTH members and pending - verify manually in Hint.")

    if friends_family_excluded:
        warnings.append(f"{friends_family_excluded} friends-and-family / comp membership(s) matched "
                        f"EXCLUDE_PLAN_SUBSTRINGS and were dropped from members + pending (not real "
                        f"new members). Adjust EXCLUDE_PLAN_SUBSTRINGS if the plan name differs.")

    if members["total"] != anchored_paid["total"]:
        warnings.append(f"new-member count by SIGNUP date ({signup_basis_field}) = {members['total']}, "
                        f"but by billing anchor (start_date) = {anchored_paid['total']}. The gap is "
                        f"renewals / reactivations / plan-changes / future-dated starts that anchor this "
                        f"month without being new signups. Dashboard uses the signup-date count.")

    if members["total"] and members_source["other"] == members["total"] and lead_source_field is None:
        warnings.append("No Lead Source field found on patient objects via the API - it may be UI-only. "
                        "Falling back: members counted, but lead-source split is all 'other'. "
                        "Check --probe output; if blank, keep the browser read for lead source only.")

    # Optional: real consults from completed appointments this month (bonus).
    # 2026-07-10 fix: the old logic counted EVERY non-cancelled appointment as a
    # "consult" (follow-ups, member visits, everything), which wildly overstated
    # consult volume on the dashboard. Now:
    #   feed["consults"]     = consult-type appointments ONLY. Per Charlie, the free
    #                          consult is the practice's only 30-minute appointment
    #                          type and the Hint API doesn't expose appointment type,
    #                          so the rule is: duration == 30 min (from start/end),
    #                          OR title/description contains "consult" as a fallback.
    #   feed["appointments"] = the old practice-wide count (all types), same shape.
    # A duration histogram (minutes -> count) is exported for sanity-checking.
    # PHI note: aggregate counts only — titles and names are never exported.
    consults = None
    appointments = None
    consults_legacy_30min = None
    consults_running = None
    appts = list_appointments(month_start, month_end)
    if appts is not None:
        CANCELLED = ("cancel", "declin", "no_show", "no-show", "noshow", "reschedul")
        START_KEYS = ("start_at", "starts_at", "start", "start_time", "scheduled_at", "date", "start_date")
        END_KEYS = ("end_at", "ends_at", "end", "end_time", "end_date")
        now_dt = datetime.now(timezone.utc)
        start_field = None

        def _parse_dt(raw):
            try:
                d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
            except Exception:
                return None

        def _new_bucket():
            return {"occurred": 0, "scheduled": 0, "status_counts": {}}

        tot = _new_bucket()      # all appointments (practice-wide volume)
        con = _new_bucket()      # real consults: non-staff attendee is still a Contact
        legacy = _new_bucket()   # OLD 30-min heuristic — exported for comparison only
        duration_histogram = {}

        for a in appts:
            st = (a.get("status") or "unknown").lower()
            cancelled = any(t in st for t in CANCELLED)
            sraw = None
            for k in START_KEYS:
                if a.get(k):
                    sraw = a.get(k)
                    start_field = start_field or k
                    break
            eraw = None
            for k in END_KEYS:
                if a.get(k):
                    eraw = a.get(k)
                    break
            sd = _parse_dt(sraw) if sraw else None
            ed = _parse_dt(eraw) if eraw else None
            started_appt = bool(sd and sd <= now_dt)
            duration_min = None
            if sd and ed:
                duration_min = int(round((ed - sd).total_seconds() / 60))
                dk = str(duration_min)
                duration_histogram[dk] = duration_histogram.get(dk, 0) + 1
            text_blob = " ".join(str(a.get(k) or "") for k in ("title", "description")).lower()
            # REAL consult signal: the non-staff attendee is still a Contact (no patient id),
            # i.e. an actual prospect. Same rule consult_count.py / send_daily_summary use.
            is_consult = _is_consult_booking(a)
            # Legacy rule kept ONLY to export the gap: 30 min is NOT unique to consults
            # (follow-ups and member visits share the slot), which is why July read 14.
            legacy_is_consult = (duration_min == 30) or ("consult" in text_blob)
            for bucket, applies in ((tot, True), (con, is_consult), (legacy, legacy_is_consult)):
                if not applies:
                    continue
                bucket["status_counts"][st] = bucket["status_counts"].get(st, 0) + 1
                if not cancelled:
                    bucket["scheduled"] += 1
                    if started_appt:
                        bucket["occurred"] += 1

        def _block(b):
            return {
                "completed_mtd": b["occurred"],   # already happened & not cancelled
                "scheduled_mtd": b["scheduled"],  # all non-cancelled this month (incl. upcoming)
                "status_counts": b["status_counts"],
                "start_field": start_field,
                "appointment_keys": sorted(appts[0].keys()) if appts else [],
            }

        consults = _block(con)
        consults["match_rule"] = ("non-staff attendee is still a Contact (no patient id) = a real "
                                  "prospect consult; same rule as consult_count.is_consult_booking")
        consults["basis"] = "contact_attendee_window"
        appointments = _block(tot)
        appointments["appointments_seen"] = len(appts)
        appointments["duration_histogram"] = duration_histogram  # minutes -> count (sanity check)

        # OLD basis, exported so the over-count stays visible instead of silent.
        consults_legacy_30min = _block(legacy)
        consults_legacy_30min["match_rule"] = "LEGACY/WRONG: duration == 30 min OR 'consult' in title/description"

        # Erasure-proof running tally: Hint DELETES the Contact attendee once a prospect
        # enrols, so a converted consult retroactively looks like a member visit and the
        # window count above UNDER-reports. consult_count.py captures consults while they're
        # still Contacts on each daily run and persists a monthly tally that never decays --
        # that's the authoritative "consults booked this month" number.
        consults_running = read_consult_tally(period)
        if consults_running is not None:
            consults["booked_mtd_running_tally"] = consults_running["mtd_count"]
            consults["tally_state_written"] = consults_running["state_written"]
            consults["basis"] = "contact_attendee_running_tally"
            if consults_running["stale_days"] is not None and consults_running["stale_days"] >= 2:
                warnings.append(
                    f"consult_count_state.json was last written {consults_running['state_written']} "
                    f"(~{consults_running['stale_days']}d ago) - the daily summary poller may have "
                    f"stopped. The running consult tally only advances when it runs, so "
                    f"booked_mtd_running_tally ({consults_running['mtd_count']}) may be UNDER-counting.")
        else:
            warnings.append("No consult_count_state.json for this month - falling back to the "
                            "in-window Contact-attendee count, which UNDER-reports consults that "
                            "already converted to members (Hint erases the Contact attendee on enrol).")

        if consults_legacy_30min["scheduled_mtd"] != consults["scheduled_mtd"]:
            warnings.append(
                f"consults by REAL signal (Contact attendee) = {consults['scheduled_mtd']} in-window"
                + (f" / {consults_running['mtd_count']} booked-MTD running tally" if consults_running else "")
                + f", but the OLD 30-min heuristic = {consults_legacy_30min['scheduled_mtd']}. "
                f"30 min is not unique to consults (follow-ups/member visits share the slot), so the "
                f"old rule over-counted. Dashboard now uses the Contact-attendee basis.")
    else:
        warnings.append("appointments endpoint unavailable - consults not exported this run.")

    feed = {
        "generated_at": now.isoformat(),
        "period": period,
        "source": "hint-api",
        "active_members": active_members,                # WHOLE-BOOK active counts (North Star: concierge vs 300 goal)
        "terminations_mtd": terminations,                # memberships ENDED this month (net growth = members − this)
        "members": members,                              # PAID only (unless members_counted_basis == "all_fallback")
        "members_split": {"concierge": members["concierge"], "so": members["so"]},
        "members_source": members_source,
        "members_pending": pending,                      # membership created but NO payment method on file
        "members_counted_basis": members_counted_basis,  # "paid" | "all_fallback"
        "new_member_basis": signup_basis_field,          # "created_at" (real signups) | "start_date" (fallback)
        "members_anchored_by_start_date": anchored_paid, # OLD basis: paid memberships whose start_date is this month (comparison)
        "payment_unknown": payment_unknown,              # payment status unverifiable (API error); excluded from both
        "pending_future_dated": pending_future_dated,    # separate diagnostic: status=='pending' or future-dated start
        "lead_source_field": lead_source_field,         # which patient key held it (or null)
        "lead_source_unmapped": lead_source_unmapped,    # raw values that fell to "other"
        "excluded_count": excluded_count,
        "friends_family_excluded": friends_family_excluded,   # comp/F&F memberships dropped from the count
        "ambiguous_plans": sorted(set(ambiguous_plans)),
        "revenue_mtd": revenue_mtd,       # collected payments this month (profitability widget)
        "lsa_test": lsa_test,          # cumulative LSA-attributed patients since LSA_TEST_SINCE (kill/keep tile)
        "consults": consults,          # REAL prospect consults (Contact attendee); see match_rule/basis
        "consults_legacy_30min": consults_legacy_30min,  # OLD 30-min heuristic — comparison only, do NOT use
        "appointments": appointments,  # practice-wide appointment volume (all types)
        "warnings": warnings,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps({k: feed[k] for k in ("period", "active_members", "terminations_mtd", "members", "new_member_basis",
                                           "members_anchored_by_start_date", "members_pending",
                                           "members_counted_basis", "payment_unknown",
                                           "friends_family_excluded",
                                           "members_source", "pending_future_dated",
                                           "lead_source_field", "revenue_mtd", "lsa_test", "consults", "consults_legacy_30min",
                                           "appointments", "warnings")}, indent=2))

    if args.probe:
        print("\n--- PROBE: lead-source field discovery (no names) ---")
        print(f"lead_source_field detected: {lead_source_field!r}")
        for i, row in enumerate(probe_rows, 1):
            print(f"  member {i}: plan={row['plan_bucket']:9s} field={row['field']!r} "
                  f"raw={row['lead_source_raw']!r} -> {row['mapped']}")
        if lead_source_unmapped:
            print(f"unmapped raw values (-> 'other'): {lead_source_unmapped}")


if __name__ == "__main__":
    main()
