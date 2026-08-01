#!/usr/bin/env python3
"""Refresh the hardcoded lead-source catalog snapshots to the full 10 entries.

Run from mbm-review-receiver:  py _patch_lscat_snapshot_20260730.py

Both the poller and the Worker keep a HARDCODED fallback copy of Hint's
lead-source catalog, used only when the live GET /api/provider/lead_sources
fails -- so a Hint hiccup can't zero out attribution. Both snapshots were taken
on the 2026-07-18 live probe and held 7 entries.

Three have been added since and were missing from BOTH: Nextdoor (a live chip on
our booking page all along), plus Google Ads and Google Local Services (created
in Hint 2026-07-30). On the fallback path all three missed the case-insensitive
label match and degraded to "Other" + a best-effort lead_source_other -- exactly
the silent-misattribution failure the Google Ads work exists to eliminate.

IDs verified live 2026-07-30 via GET /api/provider/lead_sources (10 of 10):
    AI                       lds-peJXJhFmVpPm
    Bing                     lds-mscolHUdsQqa
    Google                   lds-wIeYV7XnMyAA
    Google Ads               lds-M8oWucmvVFjk     <- new
    Google Local Services    lds-oL7SfoqLqYk2     <- new
    Nextdoor                 lds-w1igzHod22wg     <- new
    Other                    lds-jT7W91IXG6N4
    Provider/ER Referral     lds-bjGIcIblc3Or
    Social media             lds-r80ORokvGI0U
    Word of mouth            lds-UEdkC6OtsKMs

NOTE ON THE WORKER FILE: mbm-rebuild-43f1acd5 is a gitsync MIRROR that gets
reset --hard to origin. This script patches it in place, so it must run AFTER
`git fetch && git reset --hard origin/main` and BEFORE the commit -- i.e. in the
same block as _patch_worker_gads_20260730.py. If the Worker file is absent or
already patched it is skipped, and the poller edit still applies.

Count-asserted per file; a file is only written if ALL of its replacements
matched. Backs up each file it touches.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLLER = HERE.parent / "mbm-hint-enrollment" / "webhook" / "send_consult_intake.py"
WORKER = HERE.parent / "mbm-rebuild-43f1acd5" / "src" / "book" / "hint.ts"

STAMP = "20260730-lscat"

POLLER_EDITS = [
    (
        '''_LEAD_SOURCE_FALLBACK = [
    {"id": "lds-peJXJhFmVpPm", "name": "AI"},
    {"id": "lds-mscolHUdsQqa", "name": "Bing"},
    {"id": "lds-wIeYV7XnMyAA", "name": "Google"},
    {"id": "lds-jT7W91IXG6N4", "name": "Other"},
    {"id": "lds-bjGIcIblc3Or", "name": "Provider/ER Referral"},
    {"id": "lds-r80ORokvGI0U", "name": "Social media"},
    {"id": "lds-UEdkC6OtsKMs", "name": "Word of mouth"},
]''',
        '''# Refreshed 2026-07-30 from a live GET: 10 of 10. The prior snapshot was the
# 2026-07-18 probe (7 entries) and was missing Nextdoor, Google Ads and Google
# Local Services -- on the fallback path all three missed the label match and
# degraded to "Other", silently losing the channel.
_LEAD_SOURCE_FALLBACK = [
    {"id": "lds-peJXJhFmVpPm", "name": "AI"},
    {"id": "lds-mscolHUdsQqa", "name": "Bing"},
    {"id": "lds-wIeYV7XnMyAA", "name": "Google"},
    {"id": "lds-M8oWucmvVFjk", "name": "Google Ads"},
    {"id": "lds-oL7SfoqLqYk2", "name": "Google Local Services"},
    {"id": "lds-w1igzHod22wg", "name": "Nextdoor"},
    {"id": "lds-jT7W91IXG6N4", "name": "Other"},
    {"id": "lds-bjGIcIblc3Or", "name": "Provider/ER Referral"},
    {"id": "lds-r80ORokvGI0U", "name": "Social media"},
    {"id": "lds-UEdkC6OtsKMs", "name": "Word of mouth"},
]''',
        1, "poller _LEAD_SOURCE_FALLBACK -> 10 entries",
    ),
]

WORKER_EDITS = [
    (
        '''/** Practice catalog snapshot from the 2026-07-18 live probe — used ONLY when the
 *  live GET /lead_sources fails, so a Hint outage can't zero out attribution. */
const FALLBACK_CATALOG: LeadSourceEntry[] = [
  { id: "lds-peJXJhFmVpPm", name: "AI" },
  { id: "lds-mscolHUdsQqa", name: "Bing" },
  { id: "lds-wIeYV7XnMyAA", name: "Google" },
  { id: "lds-jT7W91IXG6N4", name: "Other" },
  { id: "lds-bjGIcIblc3Or", name: "Provider/ER Referral" },
  { id: "lds-r80ORokvGI0U", name: "Social media" },
  { id: "lds-UEdkC6OtsKMs", name: "Word of mouth" },
];''',
        '''/** Practice catalog snapshot — used ONLY when the live GET /lead_sources fails,
 *  so a Hint outage can't zero out attribution. Refreshed 2026-07-30 from a live
 *  GET: 10 of 10. The prior snapshot was the 2026-07-18 probe (7 entries) and was
 *  missing Nextdoor, Google Ads and Google Local Services — on the fallback path
 *  all three missed the label match and degraded to "Other", silently losing the
 *  channel. Must stay in sync with _LEAD_SOURCE_FALLBACK in the poller
 *  (send_consult_intake.py). */
const FALLBACK_CATALOG: LeadSourceEntry[] = [
  { id: "lds-peJXJhFmVpPm", name: "AI" },
  { id: "lds-mscolHUdsQqa", name: "Bing" },
  { id: "lds-wIeYV7XnMyAA", name: "Google" },
  { id: "lds-M8oWucmvVFjk", name: "Google Ads" },
  { id: "lds-oL7SfoqLqYk2", name: "Google Local Services" },
  { id: "lds-w1igzHod22wg", name: "Nextdoor" },
  { id: "lds-jT7W91IXG6N4", name: "Other" },
  { id: "lds-bjGIcIblc3Or", name: "Provider/ER Referral" },
  { id: "lds-r80ORokvGI0U", name: "Social media" },
  { id: "lds-UEdkC6OtsKMs", name: "Word of mouth" },
];''',
        1, "Worker FALLBACK_CATALOG -> 10 entries",
    ),
]


def apply(target, edits, guard, optional=False):
    if not target.exists():
        msg = f"  skip  {target.name} not found"
        if optional:
            print(msg + " (optional target)")
            return 0
        print(f"ABORT: {target} not found", file=sys.stderr)
        return 2

    raw = target.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if guard in s:
        print(f"  skip  {target.name} (already patched)")
        return 0

    for old, new, want, label in edits:
        got = s.count(old)
        if got != want:
            print(f"ABORT [{target.name}]: '{label}' matched {got} time(s), "
                  f"expected {want}. File not modified.", file=sys.stderr)
            return 3

    for old, new, want, label in edits:
        s = s.replace(old, new, want)
        print(f"  ok    {target.name}: {label}")

    bak = target.with_name(target.name + f".bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8", newline="")
    target.write_text(s.replace("\n", "\r\n") if crlf else s,
                      encoding="utf-8", newline="")

    if target.suffix == ".py":
        import py_compile
        py_compile.compile(str(target), doraise=True)
        print(f"  wrote {target.name}  (backup {bak.name}, syntax OK)")
    else:
        print(f"  wrote {target.name}  (backup {bak.name})")
        print(f"        ^ DELETE this .bak before committing the Worker repo")
    return 0


def main():
    rc = apply(POLLER, POLLER_EDITS, '"name": "Google Ads"')
    if rc != 0:
        return rc
    rc = apply(WORKER, WORKER_EDITS, 'name: "Google Ads"', optional=True)
    if rc != 0:
        return rc
    print("\nDONE. Both fallback snapshots now carry all 10 lead sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
