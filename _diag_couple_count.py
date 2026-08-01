#!/usr/bin/env python3
"""AGGREGATE-ONLY diagnostic: are couple / family memberships being undercounted?

Prints COUNTS ONLY. No names, no patient ids, no emails, no phone numbers, no
plan names. The output is safe to paste into chat.

Run:  py _diag_couple_count.py
      py _diag_couple_count.py --no-payments    (skip the per-patient payment calls)
"""
import sys
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_dashboard_members import (  # noqa: E402
    list_memberships,
    plan_name_of,
    bucket_plan,
    is_friends_family,
    is_excluded,
    patient_name_of_membership,
    has_payment_source,
)


def main():
    check_payments = "--no-payments" not in sys.argv

    try:
        mems = list_memberships()
    except Exception as e:
        print(f"ERROR: could not list memberships: {e}", file=sys.stderr)
        return 1

    print(f"total membership objects returned by Hint: {len(mems)}")

    size_all = collections.Counter()
    size_active = collections.Counter()
    status_hist = collections.Counter()
    mems_by_bucket = collections.Counter()
    people_by_bucket = collections.Counter()
    active_mems = 0
    active_people = 0
    missing_mp_field = 0

    # payment posture across ACTIVE, countable memberships
    pay_yes = 0
    pay_no = 0
    pay_unknown = 0
    pay_no_by_size = collections.Counter()

    for m in mems:
        status = (m.get("status") or "unknown").lower()
        status_hist[status] += 1
        mps = m.get("membership_patients") or []
        n = len(mps)
        if n == 0:
            missing_mp_field += 1
        size_all[n] += 1

        pid, nm = patient_name_of_membership(m)
        plan = plan_name_of(m)
        if is_excluded(nm) or is_friends_family(plan):
            continue
        if status != "active":
            continue

        bucket = bucket_plan(plan)
        size_active[n] += 1
        active_mems += 1
        active_people += max(n, 1)
        mems_by_bucket[bucket] += 1
        people_by_bucket[bucket] += max(n, 1)

        if check_payments and pid:
            has = has_payment_source(pid)
            if has is True:
                pay_yes += 1
            elif has is False:
                pay_no += 1
                pay_no_by_size[n] += 1
            else:
                pay_unknown += 1

    print("\n-- membership status histogram (all memberships) --")
    for k in sorted(status_hist):
        print(f"   {k:<12} {status_hist[k]}")

    print("\n-- ALL memberships: patients per membership --")
    for k in sorted(size_all):
        print(f"   {k} patient(s) on the membership : {size_all[k]} memberships")
    if missing_mp_field:
        print(f"   (note: {missing_mp_field} memberships returned no membership_patients array)")

    print("\n-- ACTIVE, test accounts and comp/F&F excluded --")
    for k in sorted(size_active):
        print(f"   {k} patient(s) on the membership : {size_active[k]} memberships")

    print("")
    print(f"current dashboard basis (1 per membership) : {active_mems}")
    print(f"corrected basis (every person counted)     : {active_people}")
    print(f"undercount from multi-person memberships   : {active_people - active_mems}")

    print("\n-- by bucket --")
    for b in sorted(set(list(mems_by_bucket) + list(people_by_bucket))):
        print(f"   {b:<10} memberships={mems_by_bucket[b]:<5} people={people_by_bucket[b]}")

    if check_payments:
        print("\n-- payment method on file, among ACTIVE countable memberships --")
        print(f"   has a card        : {pay_yes}")
        print(f"   NO card on file   : {pay_no}")
        print(f"   could not verify  : {pay_unknown}")
        if pay_no:
            print("   of the no-card ones, by patients-per-membership:")
            for k in sorted(pay_no_by_size):
                print(f"      {k} patient(s): {pay_no_by_size[k]}")
        print("   (an ACTIVE membership with no card is what the monthly new-member")
        print("    count silently drops as 'pending' - that is the suspected bug)")
    else:
        print("\n(payment check skipped)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
