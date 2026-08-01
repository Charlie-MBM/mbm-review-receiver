#!/usr/bin/env python3
"""AGGREGATE-ONLY probe: does Hint's payment_methods endpoint expose card expiry?

v2 (2026-07-29) -- v1 CONCLUDED "NONE FOUND" TOO EARLY. It only inspected the
TOP-LEVEL keys of each payment-method object. The real shape has a nested `card`
object (30 of 42 methods) and a nested `bank_account` object (12 of 42), and an
expiry field would live INSIDE `card`, not beside it. v2 walks nested dicts to a
depth of 3 and reports dotted paths.

Prints FIELD NAMES and COUNTS only. No names, no patient ids, no card numbers, no
last-4, no emails, no phones. Safe to paste into chat.

Run:  py _diag_card_expiry.py
"""
import sys
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_dashboard_members import (  # noqa: E402
    list_memberships,
    plan_name_of,
    is_friends_family,
    is_excluded,
    patient_name_of_membership,
    get_payment_methods,
)

EXPIRY_HINTS = ("exp", "expir", "valid_thru", "valid_until", "month", "year")

# Never print the VALUE of a field whose name looks identifying.
SENSITIVE = ("last4", "last_4", "last_four", "number", "name", "email", "phone",
             "address", "zip", "postal", "fingerprint", "token", "id", "routing",
             "account")

# Values of these are safe to histogram (low-cardinality, non-identifying).
SAFE_VALUE_FIELDS = ("type", "kind", "brand", "card_type", "object", "status",
                     "default", "is_default", "funding", "country", "network")

MAX_DEPTH = 3


def flatten(obj, prefix, out, depth=0):
    """Yield dotted-path -> value for nested dicts, to MAX_DEPTH."""
    if not isinstance(obj, dict) or depth > MAX_DEPTH:
        return
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.append((path, "<dict>"))
            flatten(v, path, out, depth + 1)
        elif isinstance(v, list):
            out.append((path, f"<list[{len(v)}]>"))
            for item in v[:3]:
                if isinstance(item, dict):
                    flatten(item, path + "[]", out, depth + 1)
        else:
            out.append((path, v))


def leaf(path):
    return path.rsplit(".", 1)[-1].lower()


def main():
    try:
        mems = list_memberships()
    except Exception as e:
        print(f"ERROR: could not list memberships: {e}", file=sys.stderr)
        return 1

    pids = []
    for m in mems:
        if (m.get("status") or "").lower() != "active":
            continue
        pid, nm = patient_name_of_membership(m)
        if is_excluded(nm) or is_friends_family(plan_name_of(m)):
            continue
        if pid:
            pids.append(pid)

    print(f"active countable memberships to probe: {len(pids)}")

    key_counts = collections.Counter()
    expiry_keys = collections.Counter()
    value_hist = collections.defaultdict(collections.Counter)
    pm_total = 0
    patients_with_pm = 0
    errors = 0

    for pid in pids:
        pm = get_payment_methods(pid)
        if pm is None:
            errors += 1
            continue
        if pm:
            patients_with_pm += 1
        for obj in pm:
            if not isinstance(obj, dict):
                continue
            pm_total += 1
            flat = []
            flatten(obj, "", flat)
            for path, val in flat:
                key_counts[path] += 1
                lk = leaf(path)
                if any(h in lk for h in EXPIRY_HINTS):
                    expiry_keys[path] += 1
                    if not any(s in lk for s in SENSITIVE):
                        value_hist[path][str(val)] += 1
                elif lk in SAFE_VALUE_FIELDS:
                    value_hist[path][str(val)] += 1

    print(f"payment-method objects seen : {pm_total}")
    print(f"patients with >=1 method    : {patients_with_pm}")
    print(f"patients that errored       : {errors}")

    print("\n-- every field path seen (NESTED, dotted; count = objects that had it) --")
    for k in sorted(key_counts):
        print(f"   {k:<40} {key_counts[k]}")

    print("\n-- paths that look like an expiration date --")
    if expiry_keys:
        for k in sorted(expiry_keys):
            print(f"   {k:<40} {expiry_keys[k]}")
    else:
        print("   NONE FOUND at depth<=3 -- Hint really does not expose card expiry.")

    print("\n-- value histograms (non-identifying fields only) --")
    for k in sorted(value_hist):
        vals = value_hist[k]
        shown = ", ".join(f"{v}={c}" for v, c in sorted(vals.items())[:30])
        print(f"   {k:<40} {shown}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
