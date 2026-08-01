#!/usr/bin/env python3
"""Add aggregate CARD-EXPIRY monitoring to the members feed.

Why: 2026-07-29 a semaglutide patient's membership went "unpaid" because their card
had expired. We only found out after the charge failed. Hint DOES expose expiry --
`card.exp_month` / `card.exp_year`, confirmed on all 30 cards on file (probe run
2026-07-29) -- it is just nested one level down, which is why the first probe missed
it. This patch makes the exporter read it and report COUNTS ONLY, so an expiring card
becomes a thing you fix in advance instead of a collections problem after the fact.

PHI boundary: the feed gains counts and nothing else. No names, no patient ids, no
last-4, no expiry dates tied to a person. The hosted dashboard stays clean.

Cost: ~35 extra Hint API calls per run (one per active countable membership).

Run once from mbm-review-receiver:  py _patch_card_expiry_20260729.py
Count-asserted; aborts before writing on any mismatch. Makes a backup.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "export_dashboard_members.py"

R = []


def rep(old, new, n=1, label=""):
    R.append((old, new, n, label))


# --- 1. the scanner helper ----------------------------------------------------
rep(
    '''def has_payment_source(pat_id):
    """True = >=1 payment method on file; False = none on file; None = couldn't verify."""
    pm = get_payment_methods(pat_id)
    if pm is None:
        return None
    return len(pm) > 0''',
    '''def has_payment_source(pat_id):
    """True = >=1 payment method on file; False = none on file; None = couldn't verify."""
    pm = get_payment_methods(pat_id)
    if pm is None:
        return None
    return len(pm) > 0


def scan_card_expiry(pat_ids, now):
    """AGGREGATE card-expiry census over the given patients. COUNTS ONLY.

    Hint nests expiry inside the payment method: {"type":"card","card":{"exp_month":4,
    "exp_year":2029,...}}. It is NOT a top-level field -- that nesting is why the
    first probe reported "no expiry available" (2026-07-29).

    A card expires at the END of its exp_month, so months_left == 0 means "expires
    this month, still chargeable today". Negative means already dead.

    Returns a dict of counts, or None if nothing could be read. NEVER returns a
    patient id, a name, a last-4, or a per-person date -- this lands in a feed that
    is baked into a Cloudflare-hosted page, and Cloudflare is not BAA-covered.
    """
    out = {
        "patients_scanned": 0,
        "patients_with_card": 0,
        "patients_bank_only": 0,
        "patients_no_method": 0,
        "cards_seen": 0,
        "expired": 0,
        "expiring_this_month": 0,
        "expiring_next_month": 0,
        "expiring_within_90d": 0,
        "unreadable_expiry": 0,
        "errors": 0,
        "basis": ("card.exp_month/card.exp_year on active countable memberships; "
                  "a card is counted expired the month AFTER its exp_month; "
                  "counts only, no identifiers"),
    }
    ny, nm = now.year, now.month
    for pid in pat_ids:
        out["patients_scanned"] += 1
        pms = get_payment_methods(pid)
        if pms is None:
            out["errors"] += 1
            continue
        if not pms:
            out["patients_no_method"] += 1
            continue
        saw_card = False
        for pm in pms:
            if not isinstance(pm, dict):
                continue
            card = pm.get("card")
            if not isinstance(card, dict):
                continue
            saw_card = True
            out["cards_seen"] += 1
            try:
                em = int(card.get("exp_month"))
                ey = int(card.get("exp_year"))
            except (TypeError, ValueError):
                out["unreadable_expiry"] += 1
                continue
            if ey < 100:            # some processors send a 2-digit year
                ey += 2000
            months_left = (ey - ny) * 12 + (em - nm)
            if months_left < 0:
                out["expired"] += 1
            elif months_left == 0:
                out["expiring_this_month"] += 1
                out["expiring_within_90d"] += 1
            elif months_left == 1:
                out["expiring_next_month"] += 1
                out["expiring_within_90d"] += 1
            elif months_left == 2:
                out["expiring_within_90d"] += 1
        if saw_card:
            out["patients_with_card"] += 1
        else:
            out["patients_bank_only"] += 1
    return out''',
    1, "scan_card_expiry() helper",
)

# --- 2. collect the active countable patient ids ------------------------------
rep(
    """        _act_mem_objects = 0
        _act_multi = 0""",
    """        _act_mem_objects = 0
        _act_multi = 0
        _act_pids = []""",
    1, "active pid accumulator",
)

rep(
    """            _act_mem_objects += 1
            if _n > 1:
                _act_multi += 1""",
    """            _act_mem_objects += 1
            if _pid:
                _act_pids.append(_pid)
            if _n > 1:
                _act_multi += 1""",
    1, "collect active pids",
)

# --- 3. run the scan and attach it --------------------------------------------
rep(
    '''        active_members["multi_person_memberships"] = _act_multi''',
    '''        active_members["multi_person_memberships"] = _act_multi
        # Card-expiry census. Defensive: a failure here must never break the feed.
        # NOTE on couples: the pid here is the membership's PRIMARY patient, who is
        # the one Hint charges. The non-paying partner has no card of their own by
        # design, so scanning the primary is the correct (and complete) coverage.
        try:
            _ce = scan_card_expiry(_act_pids, now)
            active_members["card_expiry"] = _ce
            if _ce and _ce.get("expired", 0) > 0:
                warnings.append(
                    f"{_ce['expired']} active membership(s) are paying with an EXPIRED card. "
                    f"The next charge will fail and the membership will flip to 'unpaid'. "
                    f"Use Hint's 'Send Request For Payment Info' on each - never handle "
                    f"card data by hand.")
            if _ce and _ce.get("expiring_this_month", 0) + _ce.get("expiring_next_month", 0) > 0:
                warnings.append(
                    f"{_ce['expiring_this_month']} card(s) expire this month and "
                    f"{_ce['expiring_next_month']} next month. Request updated payment "
                    f"info before the charge fails, not after.")
        except Exception as _e:
            warnings.append(f"card-expiry scan failed ({_e}); expiry counts unavailable this run.")''',
    1, "attach card_expiry + warnings",
)


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if "def scan_card_expiry(" in s:
        print("ABORT: already patched (scan_card_expiry exists). Nothing to do.")
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

    bak = TARGET.with_name("export_dashboard_members.py.bak-20260729-cardexp")
    bak.write_text(raw, encoding="utf-8", newline="")
    out = s.replace("\n", "\r\n") if crlf else s
    TARGET.write_text(out, encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}")
    print(f"backup  {bak.name}")

    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("syntax  OK")
    print("\nNext:  py export_dashboard_members.py   then   py bake_dashboard.py --push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
