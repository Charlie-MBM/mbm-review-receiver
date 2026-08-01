#!/usr/bin/env python3
"""Stop counting DEAD memberships as "pending payment".

Charlie 2026-07-31: "there arent 5 pending payment patients this month ... there's
just one i can see". He was right, and _diag_pending_20260731.py proved it:

    status        memberships  people   verdict
    ended                   3       3   NOT pending
    unpaid                  1       1   NOT pending
    pending                 1       1   AWAITING PAYMENT
    -> tile should read 1, not 5

CAUSE: the new-member bucketing keeps only status == "active" out of `pending`:

    if has_pay is True or status == "active":  -> paid
    elif has_pay is False:                     -> pending
    else:                                      -> payment_unknown

So a membership created this month with no payment method on file is called "pending
payment" even when it is ENDED (signed up and bailed - already reported separately as
terminations_mtd.never_paid_excluded = 3) or UNPAID (live but not paying - a different
problem, already driving the amber billing alert). Neither is waiting to pay.

FIX: `pending` means status == "pending" only. Everything else with no payment on file
is tallied into a NEW diagnostic, members_no_payment_other, keyed by status - so the
people are never silently dropped, and an unfamiliar status shows up as itself instead
of quietly inflating a headline number.

Idempotent: re-running is a no-op. Writes a .bak first.
Run:  py _patch_pending_20260731.py
"""
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "export_dashboard_members.py"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
GUARD = "members_no_payment_other"

EDITS = [
    (
        '''            elif has_pay is False:
                pending[b] += n_people
                pending["total"] += n_people
            else:
                payment_unknown += n_people''',
        '''            elif has_pay is False and status == "pending":
                # AWAITING FIRST PAYMENT - the only thing "pending payment" may mean.
                pending[b] += n_people
                pending["total"] += n_people
            elif has_pay is False:
                # No payment method, but NOT awaiting payment: ended (signed up and
                # bailed - see terminations_mtd.never_paid_excluded) or unpaid (live
                # but not billing - see active_members.reconciliation_by_status and
                # the amber billing alert). Counting these as "pending" read 5 when
                # the true figure was 1 (Charlie, 2026-07-31). Kept per status so
                # nobody vanishes and a new status can't silently inflate a headline.
                no_payment_other[status or "(blank)"] = (
                    no_payment_other.get(status or "(blank)", 0) + n_people)
            else:
                payment_unknown += n_people''',
        1, "pending bucket: status == 'pending' only",
    ),
    (
        '''    payment_unknown = 0                                    # payment status couldn't be verified (API error)''',
        '''    payment_unknown = 0                                    # payment status couldn't be verified (API error)
    no_payment_other = {}                                  # no payment on file but NOT pending, by status''',
        1, "init no_payment_other",
    ),
    (
        '''        "payment_unknown": payment_unknown,              # payment status unverifiable (API error); excluded from both''',
        '''        "members_no_payment_other": no_payment_other,    # no payment on file but NOT awaiting payment, by status (ended/unpaid/...); these used to inflate members_pending
        "payment_unknown": payment_unknown,              # payment status unverifiable (API error); excluded from both''',
        1, "emit members_no_payment_other",
    ),
]


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2
    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if GUARD in s:
        print("  skip  export_dashboard_members.py (already patched)")
        return 0

    for old, _new, want, label in EDITS:
        got = s.count(old)
        if got != want:
            print(f"ABORT: '{label}' matched {got} time(s), expected {want}. "
                  f"File NOT modified.", file=sys.stderr)
            return 3

    for old, new, want, label in EDITS:
        s = s.replace(old, new, want)
        print(f"  ok    {label}")

    bak = TARGET.with_name(TARGET.name + f".bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8")
    TARGET.write_text(s.replace("\n", "\r\n") if crlf else s, encoding="utf-8")
    print(f"\n  backup: {bak.name}")
    print("  patched: export_dashboard_members.py")
    print("\n  Next: py export_dashboard_members.py   (then members_pending should read 1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
