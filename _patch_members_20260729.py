#!/usr/bin/env python3
"""One-shot patch: count PEOPLE, not membership objects, and stop dropping the
non-paying partner of a couple.

Run once from mbm-review-receiver:  py _patch_members_20260729.py
Every replacement is count-asserted; any mismatch aborts before writing.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "export_dashboard_members.py"

R = []


def rep(old, new, n=1, label=""):
    R.append((old, new, n, label))


# --- 1. docstring: the new-member definition changed -------------------------
rep(
    """method on file (GET /patients/{id}/payment_methods, same signal the nurture engine uses).
A membership with no payment method is "pending" and reported separately in `members_pending`
- it is NOT counted in `members`.""",
    """method on file (GET /patients/{id}/payment_methods, same signal the nurture engine uses)
OR Hint itself reports the membership ACTIVE. That second clause was added 2026-07-29: on a
couple membership the non-paying partner has NO payment method of their own -- Hint charges
the paying partner and still marks the membership active -- so a card-on-file check alone
silently dropped one real member per couple. Hint's status is the billing truth; the
card check now only decides memberships Hint has NOT marked active.
A membership that is neither active nor card-backed is "pending", reported separately in
`members_pending` - it is NOT counted in `members`. Counts are PER PATIENT, not per
membership object: Hint models a couple as ONE membership carrying TWO membership_patients
(the plan reads "per person" because that is the PRICE basis, not the object basis).""",
    1, "docstring / new-member definition",
)

# --- 2. helper: people on a membership ---------------------------------------
rep(
    '''def plan_name_of(m):
    return ((m.get("plan") or {}).get("name")) or ""''',
    '''def membership_people(m):
    """How many PATIENTS this membership covers.

    Hint models a couple as ONE membership object with TWO membership_patients.
    The plan name says "per person" because that is the PRICE basis, not the
    object basis. Counting membership objects therefore undercounted every
    couple by exactly one person (2026-07-29 fix).
    """
    mps = m.get("membership_patients") or []
    return max(len(mps), 1)


def plan_name_of(m):
    return ((m.get("plan") or {}).get("name")) or ""''',
    1, "membership_people() helper",
)

# --- 3. active-member tally: per person --------------------------------------
rep(
    """        _act = {"concierge": 0, "so": 0, "total": 0}
        _act_ff = 0
        _act_name_excluded = 0""",
    """        _act = {"concierge": 0, "so": 0, "total": 0}
        _act_ff = 0
        _act_name_excluded = 0
        _act_mem_objects = 0
        _act_multi = 0""",
    1, "active tally counters",
)

rep(
    """            _act[_b] += 1
            _act["total"] += 1
        active_members = dict(_act)""",
    """            _n = membership_people(_m)
            _act[_b] += _n
            _act["total"] += _n
            _act_mem_objects += 1
            if _n > 1:
                _act_multi += 1
        active_members = dict(_act)""",
    1, "active tally increments per person",
)

rep(
    '''        active_members["basis"] = "membership status == 'active'; comps/F&F and test accounts excluded"''',
    '''        active_members["membership_objects"] = _act_mem_objects
        active_members["multi_person_memberships"] = _act_multi
        active_members["basis"] = ("membership status == 'active', counted PER PATIENT "
                                   "(a couple = 2 people on 1 membership object); "
                                   "comps/F&F and test accounts excluded")''',
    1, "active_members basis + object counts",
)

# --- 4. new-member tally: per person, trust Hint's active status --------------
rep(
    """        if signup_in:
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
                payment_unknown += 1""",
    """        n_people = membership_people(m)

        if signup_in:
            all_members[b] += n_people     # ungated tally (used only for the systemic-failure fallback)
            all_members["total"] += n_people
            all_source[bucket] += n_people
            if raw and bucket == "other":
                lead_source_unmapped[raw] = lead_source_unmapped.get(raw, 0) + 1
            # 2026-07-29: the non-paying partner of a couple has no payment method of
            # their own; Hint bills the paying partner and still reports the membership
            # ACTIVE. Trust Hint's status first, fall back to card-on-file only for
            # memberships Hint has not marked active.
            if has_pay is True or status == "active":
                paid_members[b] += n_people
                paid_members["total"] += n_people
                paid_source[bucket] += n_people
            elif has_pay is False:
                pending[b] += n_people
                pending["total"] += n_people
            else:
                payment_unknown += n_people""",
    1, "new-member tally per person + active-status exemption",
)

rep(
    """        if started_in and has_pay is True:
            anchored_paid[b] += 1
            anchored_paid["total"] += 1""",
    """        if started_in and (has_pay is True or status == "active"):
            anchored_paid[b] += n_people
            anchored_paid["total"] += n_people""",
    1, "start_date comparison basis",
)


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if "def membership_people(" in s:
        print("ABORT: already patched (membership_people exists). Nothing to do.")
        return 1

    for old, new, want, label in R:
        got = s.count(old)
        if got != want:
            print(f"ABORT: '{label}' matched {got} time(s), expected {want}. "
                  f"File not modified.", file=sys.stderr)
            return 3

    for old, new, want, label in R:
        s = s.replace(old, new, want)
        print(f"  ok  {label}")

    bak = TARGET.with_suffix(".py.bak-20260729-couples")
    bak.write_text(raw, encoding="utf-8", newline="")
    out = s.replace("\n", "\r\n") if crlf else s
    TARGET.write_text(out, encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}")
    print(f"backup  {bak.name}")

    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("syntax  OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
