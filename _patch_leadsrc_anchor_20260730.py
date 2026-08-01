#!/usr/bin/env python3
"""Fix _diag_lead_source_dates.py to anchor the cutover on BOOKING date, not membership date.

Why: the lead-source chip / derive_lead_source() fires when the patient BOOKS
(pat_created). The membership row is created later, when they actually enroll
(mem_created) -- days or weeks after. Anchoring the cutover on mem_created
therefore mislabels a pre-cutover booking as "AFTER cutover" whenever the person
booked before the portal change but enrolled after it.

Observed 2026-07-29: the script reported "2 AFTER cutover with no lead source"
-- both were false positives. Their pat_created were 07-13 and 07-15, i.e. they
booked BEFORE the 07-18 portal change. Re-anchored, post-cutover attribution is
4 for 4, zero failures.

Falls back to mem_created only when pat_created is unreadable, so a missing
patient record degrades to the old behaviour instead of silently dropping the row.

Run once from mbm-review-receiver:  py _patch_leadsrc_anchor_20260730.py
Count-asserted; aborts before writing on any mismatch. Makes a backup.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "_diag_lead_source_dates.py"

R = []


def rep(old, new, n=1, label=""):
    R.append((old, new, n, label))


rep(
    '''            "post_cutover": bool(d_mem and d_mem >= cut),''',
    '''            # ANCHOR ON BOOKING DATE (pat_created), not membership date.
            # The lead-source chip / derive_lead_source() runs at BOOKING time, so
            # "was this lead captured under the new portal?" is a question about when
            # they booked -- not when they later enrolled. Anchoring on mem_created
            # produced two false "AFTER cutover" failures on 2026-07-29 for people who
            # booked 07-13 and 07-15. Fall back to mem_created only if pat is unreadable.
            "post_cutover": bool((d_pat or d_mem) and (d_pat or d_mem) >= cut),
            "anchor": "pat_created" if d_pat else ("mem_created" if d_mem else "none"),''',
    1, "anchor post_cutover on pat_created",
)

rep(
    '''    print(f"cutover  {cut}  (booking portal auto-assigns lead source on/after this date)\\n")''',
    '''    print(f"cutover  {cut}  (booking portal auto-assigns lead source on/after this date)")
    print("anchored on pat_created (BOOKING date) - the chip fires at booking, not at "
          "enrollment.\\n")''',
    1, "note the anchor in the header",
)

rep(
    '''    rows.sort(key=lambda r: r["mem_created"])''',
    '''    rows.sort(key=lambda r: (r["pat_created"], r["mem_created"]))''',
    1, "sort by booking date",
)


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if '"anchor": "pat_created"' in s:
        print("ABORT: already patched. Nothing to do.")
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

    bak = TARGET.with_name("_diag_lead_source_dates.py.bak-20260730-anchor")
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
