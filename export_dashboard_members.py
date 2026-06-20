#!/usr/bin/env python3
"""
export_dashboard_members.py - READ-ONLY Hint -> dashboard feed.

Pulls this-month's NEW memberships (Hint "Starting this month", membership-owner
semantics) and each member's self-reported Lead Source via the Hint API, tallies
AGGREGATE COUNTS ONLY (no names, no PHI), and writes a small JSON the MBM weekly
dashboard refresh reads in place of the old browser scrape.

NEW-MEMBER DEFINITION (2026-06): a membership counts as a real new member only if it
has BOTH a membership AND a payment method on file (GET /patients/{id}/payment_methods,
same signal the nurture engine uses). A membership with no payment method is "pending"
and reported separately in `members_pending` - it is NOT counted in `members`. If the
payment endpoint can't be reached for ANY member in a run, we fall back to counting all
memberships (old behavior) and flag it in `warnings` rather than zeroing the dashboard.

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

# The 7 dashboard lead-source buckets (must match the dashboard + Hint dropdown).
SOURCE_KEYS = ["google", "bing", "ai", "social", "provider_referral", "word_of_mouth", "other"]


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
    paid_members = {"concierge": 0, "so": 0, "total": 0}   # counted as new members
    paid_source = {k: 0 for k in SOURCE_KEYS}
    pending = {"concierge": 0, "so": 0, "total": 0}        # membership, NO payment on file
    payment_unknown = 0                                    # payment status couldn't be verified (API error)
    # Ungated tallies over every started membership — used only for the systemic-failure fallback.
    all_members = {"concierge": 0, "so": 0, "total": 0}
    all_source = {k: 0 for k in SOURCE_KEYS}
    pending_future_dated = 0
    excluded_count = 0
    ambiguous_plans = []
    lead_source_field = None
    lead_source_unmapped = {}   # raw value -> count, for anything that landed in "other"
    probe_rows = []

    try:
        all_mems = list_memberships()
    except Exception as e:
        print(f"ERROR: could not list memberships: {e}", file=sys.stderr)
        sys.exit(1)

    started = [m for m in all_mems if in_month(m.get("start_date"), y, mo)]

    for m in started:
        pat_id, name = patient_name_of_membership(m)
        if is_excluded(name):
            excluded_count += 1
            continue
        plan = plan_name_of(m)
        b = bucket_plan(plan)
        if b == "concierge" and plan and not any(t in plan.lower() for t in ("concierge", "membership", "direct primary", "dpc")):
            ambiguous_plans.append(plan)

        status = (m.get("status") or "").lower()
        try:
            future = datetime.strptime(str(m.get("start_date"))[:10], "%Y-%m-%d").date() > now.date()
        except Exception:
            future = False
        if status == "pending" or future:
            pending_future_dated += 1

        # Lead source (read once; used for the paid split and the fallback all-source split).
        raw, key = (None, None)
        if pat_id:
            pt = get_patient(pat_id)
            raw, key = extract_lead_source(pt)
            if key and not lead_source_field:
                lead_source_field = key
        bucket = map_source(raw)

        # Ungated tally (every membership that started this month).
        all_members[b] += 1
        all_members["total"] += 1
        all_source[bucket] += 1
        if raw and bucket == "other":
            lead_source_unmapped[raw] = lead_source_unmapped.get(raw, 0) + 1

        # Payment gate: a real new member needs a payment method on file.
        has_pay = has_payment_source(pat_id) if pat_id else None
        if has_pay is True:
            paid_members[b] += 1
            paid_members["total"] += 1
            paid_source[bucket] += 1
        elif has_pay is False:
            pending[b] += 1
            pending["total"] += 1
        else:
            payment_unknown += 1

        if args.probe:
            probe_rows.append({"plan_bucket": b, "lead_source_raw": raw, "field": key,
                               "mapped": bucket, "has_payment": has_pay})

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

    if members["total"] and members_source["other"] == members["total"] and lead_source_field is None:
        warnings.append("No Lead Source field found on patient objects via the API - it may be UI-only. "
                        "Falling back: members counted, but lead-source split is all 'other'. "
                        "Check --probe output; if blank, keep the browser read for lead source only.")

    # Optional: real consults from completed appointments this month (bonus).
    consults = None
    appts = list_appointments(month_start, month_end)
    if appts is not None:
        # Robust, vocabulary-independent: a "completed consult" = an appointment whose
        # start time is already in the past AND whose status isn't a cancellation. This
        # avoids guessing Hint's exact "completed" status string. We also export
        # scheduled_mtd (all non-cancelled this month, incl. upcoming) plus diagnostics.
        status_counts = {}
        occurred = 0
        scheduled = 0
        CANCELLED = ("cancel", "declin", "no_show", "no-show", "noshow", "reschedul")
        START_KEYS = ("start_at", "starts_at", "start", "start_time", "scheduled_at", "date", "start_date")
        now_dt = datetime.now(timezone.utc)
        start_field = None
        for a in appts:
            st = (a.get("status") or "unknown").lower()
            status_counts[st] = status_counts.get(st, 0) + 1
            cancelled = any(t in st for t in CANCELLED)
            if not cancelled:
                scheduled += 1
            sraw = None
            for k in START_KEYS:
                if a.get(k):
                    sraw = a.get(k)
                    start_field = start_field or k
                    break
            started = False
            if sraw:
                try:
                    sd = datetime.fromisoformat(str(sraw).replace("Z", "+00:00"))
                    if sd.tzinfo is None:
                        sd = sd.replace(tzinfo=timezone.utc)
                    started = sd <= now_dt
                except Exception:
                    started = False
            if started and not cancelled:
                occurred += 1
        consults = {
            "completed_mtd": occurred,        # already happened & not cancelled = a real consult
            "scheduled_mtd": scheduled,       # all non-cancelled this month (incl. upcoming)
            "appointments_seen": len(appts),
            "status_counts": status_counts,   # diagnostic: Hint's real status vocabulary
            "start_field": start_field,       # diagnostic: which key held the start datetime
            "appointment_keys": sorted(appts[0].keys()) if appts else [],  # diagnostic
        }
    else:
        warnings.append("appointments endpoint unavailable - consults not exported this run.")

    feed = {
        "generated_at": now.isoformat(),
        "period": period,
        "source": "hint-api",
        "members": members,                              # PAID only (unless members_counted_basis == "all_fallback")
        "members_split": {"concierge": members["concierge"], "so": members["so"]},
        "members_source": members_source,
        "members_pending": pending,                      # membership created but NO payment method on file
        "members_counted_basis": members_counted_basis,  # "paid" | "all_fallback"
        "payment_unknown": payment_unknown,              # payment status unverifiable (API error); excluded from both
        "pending_future_dated": pending_future_dated,    # separate diagnostic: status=='pending' or future-dated start
        "lead_source_field": lead_source_field,         # which patient key held it (or null)
        "lead_source_unmapped": lead_source_unmapped,    # raw values that fell to "other"
        "excluded_count": excluded_count,
        "ambiguous_plans": sorted(set(ambiguous_plans)),
        "consults": consults,
        "warnings": warnings,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps({k: feed[k] for k in ("period", "members", "members_pending",
                                           "members_counted_basis", "payment_unknown",
                                           "members_source", "pending_future_dated",
                                           "lead_source_field", "consults", "warnings")}, indent=2))

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
