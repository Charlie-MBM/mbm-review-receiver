#!/usr/bin/env python3
"""
Why does the dashboard say "5 pending payment" when Hint shows 1?

Charlie, 2026-07-31: "there arent 5 pending payment patients this month" / "there's
just one i can see".

SUSPECTED CAUSE (to be confirmed by this script, not asserted):
export_dashboard_members.py buckets a new membership like this --

    if has_pay is True or status == "active":   -> paid
    elif has_pay is False:                      -> PENDING
    else:                                       -> payment_unknown

The only status kept out of `pending` is "active". So a membership created this month
with no payment method on file lands in "pending payment" even if it is **ended,
cancelled or unpaid** -- i.e. dead, not waiting to pay. The feed's own
terminations_mtd.never_paid_excluded = 3 for July, which would exactly account for a
5 that should read 2.

This prints the STATUS HISTOGRAM of everything currently counted as pending, so we
can see the composition instead of guessing.

PHI: aggregate counts only. No names, no ids, no contact info are printed or stored.
Run:  py _diag_pending_20260731.py
"""
import datetime as dt
from collections import Counter

import export_dashboard_members as X

now = dt.datetime.now()
Y, MO = now.year, now.month

print(f"\n  PENDING-PAYMENT COMPOSITION - {Y}-{MO:02d}")
print("  " + "-" * 62)

mems = X.list_memberships()      # same paginated fetch main() uses

def signup_in_month(m):
    """Identical gate to the exporter's: created_at_of() via CREATED_KEYS, falling
    back to start_date. Using the module's own helper so this diag can never count a
    different set than the number it is explaining."""
    raw = X.created_at_of(m)
    if raw:
        return X.in_month(raw, Y, MO)
    return X.in_month(m.get("start_date"), Y, MO)

pending_status = Counter()
pending_people = Counter()
truly_pending = 0
dead_but_counted = 0
total_pending_people = 0

for m in mems:
    if not signup_in_month(m):
        continue
    pat_id, name = X.patient_name_of_membership(m)
    if X.is_excluded(name):
        continue
    plan = X.plan_name_of(m)
    if X.is_friends_family(plan):
        continue

    status = (m.get("status") or "").lower()
    has_pay = X.has_payment_source(pat_id) if pat_id else None
    n = X.membership_people(m)

    # the EXACT condition the exporter uses for "pending payment"
    if has_pay is True or status == "active":
        continue
    if has_pay is False:
        pending_status[status or "(blank)"] += 1
        pending_people[status or "(blank)"] += n
        total_pending_people += n
        if status == "pending":
            truly_pending += n
        else:
            dead_but_counted += n

print(f"  currently counted as 'pending payment': {total_pending_people} people\n")
print(f"  {'status':<16}{'memberships':>12}{'people':>9}   verdict")
print("  " + "-" * 62)
for st, cnt in pending_status.most_common():
    verdict = "AWAITING PAYMENT" if st == "pending" else "NOT pending - should be excluded"
    print(f"  {st:<16}{cnt:>12}{pending_people[st]:>9}   {verdict}")

print("  " + "-" * 62)
print(f"  genuinely awaiting payment : {truly_pending}")
print(f"  dead/terminal, miscounted  : {dead_but_counted}")
print()
if dead_but_counted:
    print(f"  => the tile should read {truly_pending}, not {total_pending_people}.")
    print(f"     Fix: exclude terminal statuses from the pending bucket in")
    print(f"     export_dashboard_members.py (keep status == 'pending' only).")
else:
    print("  => composition does NOT explain the gap; the cause is elsewhere.")
    print("     Next suspect: per-patient vs per-membership counting (couples), or")
    print("     Hint's patient-level Pending filter meaning something different from")
    print("     membership status.")
print()
