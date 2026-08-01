#!/usr/bin/env python3
"""Re-bake dashboard_index.html from the corrected members_feed.json (couples fix).

Run once from mbm-review-receiver:  py _patch_dash_couples_20260729.py
Count-asserted; aborts before writing on any mismatch. Makes a backup.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "dashboard_index.html"

R = []


def rep(old, new, n=1, label=""):
    R.append((old, new, n, label))


rep(
    '    hint:   {as_of:"2026-07-29T16:15Z", cadence_h:26,',
    '    hint:   {as_of:"2026-07-29T17:55Z", cadence_h:26,',
    1, "hint freshness as_of",
)

rep(
    """    // comps/F&F (11) and test accounts excluded. getActive() prefers this over manual
    // entry unless a manual entry is strictly newer. Feed status histogram this run:
    // active 48 / pending 2 / unpaid 2 / ended 7 (48 = 36 real + 11 F&F + 1 test excluded).
    active: {concierge:16, so:20, as_of:"2026-07-29T16:15Z", source:"hint"},""",
    """    // comps/F&F (11) and test accounts excluded. COUNTED PER PATIENT as of 2026-07-29:
    // Hint models a couple as ONE membership object carrying TWO membership_patients
    // (the plan reads "per person" because that is the PRICE basis, not the object
    // basis), so the old per-membership tally hid exactly one real person per couple.
    // getActive() prefers this over manual entry unless a manual entry is strictly newer.
    // Feed status histogram this run: active 47 / pending 2 / unpaid 3 / ended 8
    // (47 active = 35 countable membership objects + 11 F&F + 1 test excluded;
    //  those 35 objects carry 40 people — 5 of them are two-person memberships).
    active: {concierge:20, so:20, as_of:"2026-07-29T17:55Z", source:"hint"},""",
    1, "active totals -> per-patient (concierge 16 -> 20)",
)

rep(
    '    active_recon: {as_of:"2026-07-29T16:15Z", unpaid_concierge:2, pending_concierge:2,',
    '    active_recon: {as_of:"2026-07-29T17:55Z", unpaid_concierge:2, pending_concierge:2,',
    1, "active_recon as_of",
)

rep(
    """    new_paid: {concierge:3, so:3},   // July MTD, created_at basis, payment on file
    pending:  {concierge:4, so:1, total:5},""",
    """    new_paid: {concierge:4, so:4},   // July MTD, created_at basis, per patient; Hint-active OR card on file
    pending:  {concierge:4, so:0, total:4},""",
    1, "new_paid + pending",
)

rep(
    '    basis: "signup date (created_at) + payment method on file; Friends & Family comps excluded",',
    '    basis: "signup date (created_at); counted PER PATIENT (a couple = 2); membership is Hint-active OR has a payment method on file; Friends & Family comps excluded",',
    1, "members basis string",
)

rep(
    """    source_mix: {google:2, google_lsa:0, bing:0, ai:0, social:0, provider_referral:0, word_of_mouth:0, other:4},
    lead_source_gap: "2 of 6 July members map to Google in Hint Lead Source; 4 fall in 'other' (unrecorded or off-channel). Lead Source at signup still the gap.\"""",
    """    source_mix: {google:3, google_lsa:0, bing:0, ai:0, social:0, provider_referral:0, word_of_mouth:0, other:5},
    lead_source_gap: "3 of 8 July members map to Google in Hint Lead Source; 5 fall in 'other' (unrecorded or off-channel). Lead Source at signup still the gap.\"""",
    1, "source_mix + lead_source_gap",
)

rep('const DATA_VERSION = "2026-07-29e";',
    'const DATA_VERSION = "2026-07-29f";', 1, "DATA_VERSION bump (forces client reseed)")

rep('  baked_at: "2026-07-29T17:25Z",',
    '  baked_at: "2026-07-29T18:05Z",', 1, "baked_at")


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if 'active: {concierge:20, so:20' in s:
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

    bak = TARGET.with_name("dashboard_index.html.bak-20260729-couples")
    bak.write_text(raw, encoding="utf-8", newline="")
    out = s.replace("\n", "\r\n") if crlf else s
    TARGET.write_text(out, encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}  ({len(out)} bytes)")
    print(f"backup  {bak.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
