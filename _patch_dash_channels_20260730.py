#!/usr/bin/env python3
"""Make the dashboard's channel bar render google_ads AND nextdoor.

WHY THIS EXISTS: the exporter's SOURCE_KEYS is the feed's contract, but the
dashboard does NOT read it. The channel list is hardcoded in THREE separate
places, so adding a bucket to the exporter alone populates the feed and renders
nothing:

  1. bake_dashboard.py  -- the tuple that builds the source_mix literal. Any key
     not in this tuple is DROPPED at bake time, before the browser ever sees it.
  2. dashboard_index.html  -- the SNAPSHOT source_mix literal (pre-bake default).
  3. dashboard_index.html  -- the `order` array that drives the bar + legend.

FOUND WHILE DOING THIS: `nextdoor` has been in the exporter's SOURCE_KEYS but in
NONE of the three lists above. Every Nextdoor-attributed member has therefore
been silently vanishing from the channel bar -- not folded into "other", just
gone, which also means the bar's segments have not summed to the member count.
Fixing that here too, since it is the same one-line-per-list omission and leaving
it would mean re-opening these exact three anchors next month.

PALETTE NOTE: google_ads takes MBM gold (#C9950C) -- it is the channel we spend
real money on and it should be the one your eye lands on. provider_referral moves
off #9C6F10 (dark olive-gold) to #7A5A2E (brown) so the legend does not carry two
near-identical golds side by side. nextdoor takes #96B41E, a lime distinct from
both word_of_mouth's sage #61A879 and google's forest #20713F.

Order matches the exporter's SOURCE_KEYS exactly, so the two never drift again.

Run once from mbm-review-receiver:  py _patch_dash_channels_20260730.py
Count-asserted per file; a file is only written if ALL of its replacements
matched. Backs up each file it touches.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BAKE = HERE / "bake_dashboard.py"
HTML = HERE / "dashboard_index.html"

STAMP = "20260730-chan"

BAKE_EDITS = [
    (
        '''              ("google", "google_lsa", "bing", "ai", "social",
               "provider_referral", "word_of_mouth", "other")) + "},",''',
        '''              # MUST stay in sync with SOURCE_KEYS in export_dashboard_members.py
              # AND with the `order` array in dashboard_index.html. A key missing
              # from this tuple is dropped at bake time and renders nowhere --
              # that is exactly how `nextdoor` went missing until 2026-07-30.
              ("google", "google_ads", "google_lsa", "bing", "ai", "social",
               "nextdoor", "provider_referral", "word_of_mouth",
               "other")) + "},",''',
        1, "bake source_mix key tuple",
    ),
]

HTML_EDITS = [
    (
        '''    source_mix: {google:3, google_lsa:0, bing:0, ai:0, social:0, provider_referral:0, word_of_mouth:0, other:5},''',
        '''    source_mix: {google:3, google_ads:0, google_lsa:0, bing:0, ai:0, social:0, nextdoor:0, provider_referral:0, word_of_mouth:0, other:5},''',
        1, "SNAPSHOT source_mix literal",
    ),
    (
        '''  const order=[["google","Google","#20713F"],["google_lsa","Google Local Services","#8A4A7D"],["bing","Bing","#2C6FC4"],["ai","AI","#7059B3"],["social","Social media","#4A8DB5"],["provider_referral","Provider/ER referral","#9C6F10"],["word_of_mouth","Word of mouth","#61A879"],["other","Other / not recorded","#C2703F"]];''',
        '''  // Order mirrors SOURCE_KEYS in export_dashboard_members.py. "Google (organic)"
  // is relabelled because it is now genuinely the organic-only bucket: a booking
  // carrying a Google click id (gclid/gbraid/wbraid) or a paid utm_medium is
  // classified "Google Ads" server-side and never lands here.
  const order=[["google","Google (organic)","#20713F"],["google_ads","Google Ads (paid)","#C9950C"],["google_lsa","Google Local Services","#8A4A7D"],["bing","Bing","#2C6FC4"],["ai","AI","#7059B3"],["social","Social media","#4A8DB5"],["nextdoor","Nextdoor","#96B41E"],["provider_referral","Provider/ER referral","#7A5A2E"],["word_of_mouth","Word of mouth","#61A879"],["other","Other / not recorded","#C2703F"]];''',
        1, "channel order array",
    ),
]


def apply(target, edits, guard):
    if not target.exists():
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
    return 0


def main():
    rc = apply(BAKE, BAKE_EDITS, '"google_ads", "google_lsa"')
    if rc != 0:
        return rc
    rc = apply(HTML, HTML_EDITS, '"google_ads","Google Ads (paid)"')
    if rc != 0:
        return rc

    print("\nDONE. Dashboard will now render Google Ads and Nextdoor.")
    print("Next:  py export_dashboard_members.py   then   py bake_dashboard.py --push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
