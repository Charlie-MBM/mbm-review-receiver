#!/usr/bin/env python3
"""WHEN did the members with a missing lead source sign up?

Charlie's question (2026-07-29): the booking portal was updated so every lead gets a
lead source assigned automatically from how they reached the site. If the members
showing 'other' all signed up BEFORE that change, there is no bug -- they are just
pre-change records. If any signed up AFTER, the auto-assignment is broken.

PHI-SAFE. Prints DATES, plan buckets, and lead-source buckets only. Never a name,
never a patient id, never a phone. Safe to paste into chat.

Run:  py _diag_lead_source_dates.py
      py _diag_lead_source_dates.py --cutover 2026-07-18
      py _diag_lead_source_dates.py --period 2026-06
"""
import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_dashboard_members import (  # noqa: E402
    list_memberships,
    get_patient,
    plan_name_of,
    bucket_plan,
    is_friends_family,
    is_excluded,
    patient_name_of_membership,
    patient_source_bucket,
    membership_people,
    created_at_of,
    in_month,
)

# Booking-portal GO date: every Book / Become-a-member CTA sitewide repointed to
# mtbakermedical.com/book-beta, which self-reports a lead source.
DEFAULT_CUTOVER = "2026-07-18"

PATIENT_CREATED_KEYS = ("created_at", "created", "createdAt", "created_on",
                        "registered_at", "signed_up_at")


def daystr(v):
    s = str(v or "")[:10]
    return s if len(s) == 10 and s[4] == "-" else "(none)"


def to_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def patient_created(pt):
    if not isinstance(pt, dict):
        return None
    for k in PATIENT_CREATED_KEYS:
        if pt.get(k):
            return pt[k]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutover", default=DEFAULT_CUTOVER,
                    help=f"booking-portal go-live date (default {DEFAULT_CUTOVER})")
    ap.add_argument("--period", default=None, help="YYYY-MM (default: current month)")
    args = ap.parse_args()

    cut = to_date(args.cutover)
    if not cut:
        print(f"ABORT: bad --cutover {args.cutover!r}", file=sys.stderr)
        return 2

    if args.period:
        try:
            y, mo = int(args.period[:4]), int(args.period[5:7])
        except Exception:
            print(f"ABORT: bad --period {args.period!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now()
        y, mo = now.year, now.month

    print(f"period   {y:04d}-{mo:02d}")
    print(f"cutover  {cut}  (booking portal auto-assigns lead source on/after this date)")
    print("anchored on pat_created (BOOKING date) - the chip fires at booking, not at "
          "enrollment.\n")

    try:
        mems = list_memberships()
    except Exception as e:
        print(f"ERROR: could not list memberships: {e}", file=sys.stderr)
        return 1

    rows = []
    for m in mems:
        raw_created = created_at_of(m)
        if not in_month(raw_created, y, mo):
            continue
        pid, nm = patient_name_of_membership(m)
        if is_excluded(nm):
            continue
        plan = plan_name_of(m)
        if is_friends_family(plan):
            continue

        pt = get_patient(pid) if pid else None
        raw, key, bucket = patient_source_bucket(pt) if pt else (None, None, "other")
        pc = patient_created(pt)

        d_mem = to_date(raw_created)
        d_pat = to_date(pc)
        gap = (d_mem - d_pat).days if (d_mem and d_pat) else None

        rows.append({
            "mem_created": daystr(raw_created),
            "pat_created": daystr(pc),
            "gap_days": gap,
            "plan": bucket_plan(plan),
            "people": membership_people(m),
            "bucket": bucket,
            "raw_present": bool(raw),
            "field": key or "-",
            # ANCHOR ON BOOKING DATE (pat_created), not membership date.
            # The lead-source chip / derive_lead_source() runs at BOOKING time, so
            # "was this lead captured under the new portal?" is a question about when
            # they booked -- not when they later enrolled. Anchoring on mem_created
            # produced two false "AFTER cutover" failures on 2026-07-29 for people who
            # booked 07-13 and 07-15. Fall back to mem_created only if pat is unreadable.
            "post_cutover": bool((d_pat or d_mem) and (d_pat or d_mem) >= cut),
            "anchor": "pat_created" if d_pat else ("mem_created" if d_mem else "none"),
        })

    rows.sort(key=lambda r: (r["pat_created"], r["mem_created"]))

    print(f"{'mem_created':<13}{'pat_created':<13}{'gap':>5}  {'plan':<10}"
          f"{'ppl':>4}  {'lead_source':<18}{'raw?':<6}{'vs cutover'}")
    print("-" * 88)
    for r in rows:
        gap = "-" if r["gap_days"] is None else str(r["gap_days"])
        print(f"{r['mem_created']:<13}{r['pat_created']:<13}{gap:>5}  {r['plan']:<10}"
              f"{r['people']:>4}  {r['bucket']:<18}{('yes' if r['raw_present'] else 'NO'):<6}"
              f"{'AFTER' if r['post_cutover'] else 'before'}")

    # --- the actual verdict ----------------------------------------------------
    missing = [r for r in rows if not r["raw_present"]]
    miss_after = [r for r in missing if r["post_cutover"]]
    miss_before = [r for r in missing if not r["post_cutover"]]
    have = [r for r in rows if r["raw_present"]]

    print("\n=== SUMMARY (memberships, not people) ===")
    print(f"  memberships in period            : {len(rows)}  "
          f"({sum(r['people'] for r in rows)} people)")
    print(f"  WITH a lead source in Hint       : {len(have)}")
    print(f"  MISSING a lead source            : {len(missing)}")
    print(f"     of those, signed up BEFORE cutover : {len(miss_before)}  <- expected, no bug")
    print(f"     of those, signed up AFTER cutover  : {len(miss_after)}  <- these are the problem")

    print("\n  lead-source bucket tally:")
    for k, v in sorted(Counter(r["bucket"] for r in rows).items()):
        print(f"     {k:<20} {v}")

    print("\n  gap = membership_created - patient_created, in days.")
    print("  A gap of several days means the patient record already existed from a")
    print("  book-beta consult booking (portal path). A gap of 0 means patient and")
    print("  membership were created together = a direct Hint signup, which never")
    print("  passes through the booking portal and so never gets a chip.")
    g0 = [r for r in rows if r["gap_days"] == 0]
    gp = [r for r in rows if r["gap_days"] and r["gap_days"] > 0]
    print(f"     gap 0 (direct Hint signup)     : {len(g0)}  "
          f"({sum(1 for r in g0 if not r['raw_present'])} of them missing lead source)")
    print(f"     gap > 0 (came via booking)     : {len(gp)}  "
          f"({sum(1 for r in gp if not r['raw_present'])} of them missing lead source)")

    print("\n=== VERDICT ===")
    if not missing:
        print("  Every membership this period carries a lead source. Nothing to fix.")
    elif not miss_after:
        print("  All missing lead sources predate the cutover. The portal change looks")
        print("  fine; these are legacy records. Backfill them by hand or leave them.")
    else:
        print(f"  *** {len(miss_after)} membership(s) signed up AFTER the cutover with NO lead")
        print("  source. The auto-assignment is not covering them. Check the gap column:")
        print("  gap 0 = they never touched the booking portal (direct Hint signup - the")
        print("  portal cannot fix that, the Hint signup form needs the field), gap > 0 =")
        print("  they DID come through booking and the write is failing. ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
