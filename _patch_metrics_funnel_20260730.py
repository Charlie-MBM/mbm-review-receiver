#!/usr/bin/env python3
"""DASHBOARD_METRICS.md: bring section 1's funnel contract back in line with the data.

Run from mbm-review-receiver:  py _patch_metrics_funnel_20260730.py

WHY: the doc froze on the 2026-07-28 pull (Jul 18-27, 49 tapped / 49 reached the
page) and asserts as a RULE that "the click -> page step is 1:1, there is no
click-to-page leak; do not go hunting for one." Two days later the live numbers
are 53 tapped / 56 reached, and the dashboard was dividing one by the other and
printing "106% of the step before" -- a funnel that grows.

The 1:1 was never a property of the funnel. It was a coincidence of two
independent numbers passing each other. The two rows measure DIFFERENT, NON-NESTED
populations:
  * /book-beta is reachable with no tap at all -- the front-desk ?staff=1
    bookmark, the Spruce confirmation link, the GBP profile link, typed and
    bookmarked URLs.
  * people = GA4 totalUsers = unique BROWSERS. A tap on a phone that finishes on
    a laptop is two users, only one of which tapped.
So the tap count can exceed OR trail the page count and no ratio between them is
honest in either direction.

ALSO RECORDED HERE (verified 2026-07-29 GA4 pull):
  * booking_start is a PAGEVIEW ALIAS -- starts_events 98 == page_views 98 and
    starts_people 56 == page_users 56, exactly. It fires on load. It means
    "reached the page", never "started booking", and carries zero information the
    pageview does not.
  * gads.mtd.clicks 302 vs sum(daily.ads_clicks) 300 is NOT a discrepancy: the
    daily array is Jul 1-28 and sums to 300, the five campaign rows sum to 302,
    and the MTD pull ran 2026-07-29T16:41Z picking up 2 clicks from Jul 29.
    Different end dates. Do not "fix" it.
  * GBP taps were stored twice (gbp_attrib.taps and the channel_econ GBP row) and
    drifted 4 vs 6 because only the table gets refreshed. The renderer now derives
    taps from the table.

The render-side fixes shipped in dashboard_index.html via
_patch_dash_funnel_20260730.py (DATA_VERSION 2026-07-30c). This patch is the
contract half of the same change.

Count-asserted; aborts before writing on any mismatch. Backs up the file.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "DASHBOARD_METRICS.md"
STAMP = "20260730-funnel"
GUARD = "not nested, and no ratio between them is honest"

NEW_HEADER = (
    "**Last updated:** 2026-07-30 (DATA_VERSION `2026-07-30c` — "
    "**FUNNEL STRUCTURE CORRECTED — the tap row is an input signal, not a stage.** "
    "The people-first funnel was dividing `starts_people` by `clicks_people` and "
    "rendering **\"106% of the step before\"**: on 2026-07-30 the live numbers were "
    "53 tapped / 56 reached the page, where the 2026-07-28 build had 49 / 49 and the "
    "doc had hardened that coincidence into a rule (\"1:1, no click-to-page leak\"). "
    "The two rows measure different, NON-NESTED populations — `/book-beta` is "
    "reachable with no tap (front-desk `?staff=1` bookmark, Spruce confirmation link, "
    "GBP profile link, typed/bookmarked URLs) and `people` = unique BROWSERS, so a tap "
    "on a phone finishing on a laptop is two users one of whom tapped. Fixed in three "
    "layers: (a) STRUCTURAL — the tap row carries `input:true` (renders with its share "
    "of visitors, starts no ratio) and the page row carries `funnelStart:true` (the "
    "nested funnel begins there: 56 → 10 → 9 → 9); (b) GUARD — any computed conversion "
    "above 100% anywhere in the list now renders \"not nested — exceeds the step above\" "
    "instead of a number, and the worst-drop detector ignores drops measured off an "
    "input row; (c) HONEST COPY — the leak box's hardcoded 1:1 claim is replaced by "
    "computed text stating both numbers and explaining the gap. Also: `booking_start` is "
    "confirmed a **PAGEVIEW ALIAS** (98 events == 98 page_views, 56 people == 56 "
    "page_users, exactly — it fires on load, so it is \"reached the page\", never "
    "\"started booking\"); three hardcoded labels that had drifted off their own data are "
    "now computed — the window caption (\"10 days\" against an 11-day Jul 18–28 window), "
    "the funnel section note (same window hardcoded a second time in static prose), and "
    "the GBP engagement line (\"Jul 1–15\" against `gbp_perf.window` = \"Jul 1–28\"); and "
    "GBP taps, stored twice and drifted 4 vs 6, are now derived from the `channel_econ` "
    "row with its own as-of. **Checked and deliberately NOT changed:** `gads.mtd.clicks` "
    "302 vs `sum(daily.ads_clicks)` 300 — the daily array is Jul 1–28 and sums to 300, "
    "the five campaign rows sum to 302, and the MTD pull ran 2026-07-29T16:41Z picking up "
    "2 clicks from Jul 29. Different end dates, both correct. "
    "Prior `2026-07-28b` — **FUNNEL REBUILT"
)

EDITS = [
    (
        "**Last updated:** 2026-07-28 (DATA_VERSION `2026-07-28b` — **FUNNEL REBUILT",
        NEW_HEADER,
        1, "header: new 2026-07-30c version block",
    ),
    (
        "| Rendered stage (in order) | Keys | GA4 event | Jul 18–27 people / events |\n"
        "|---|---|---|---|\n"
        "| Tapped \"Book a free consult\" | `clicks_people` / `clicks_events` | `booking_click` | 49 / 113 |\n"
        "| Reached the booking page | `starts_people` / `starts_events` | `booking_start` | 49 / 86 |\n"
        "| Picked a time | `step1_people` / `step1_events` | `booking_step_1` | 10 / 19 |\n"
        "| Entered their details | `step2_people` / `step2_events` | `booking_step_2` | 8 / 15 |\n"
        "| Booked | `completes_server_people` / `completes_server_events` | `booking_complete_server` | 8 / 14 |",

        "| Rendered stage (in order) | Keys | GA4 event | Jul 18–28 people / events |\n"
        "|---|---|---|---|\n"
        "| Tapped \"Book a free consult\" — **`input:true`: a signal, NOT a nested stage.** Renders with its share of visitors; no ratio is taken between it and the row below | `clicks_people` / `clicks_events` | `booking_click` | 53 / 121 |\n"
        "| Reached the booking page — **`funnelStart:true`: the nested funnel begins HERE.** Renders \"start of the booking flow\", not a percentage | `starts_people` / `starts_events` | `booking_start` *(pageview alias — fires on load)* | 56 / 98 |\n"
        "| Picked a time | `step1_people` / `step1_events` | `booking_step_1` | 10 / 19 |\n"
        "| Entered their details | `step2_people` / `step2_events` | `booking_step_2` | 9 / 16 |\n"
        "| Booked | `completes_server_people` / `completes_server_events` | `booking_complete_server` | 9 / 15 |",
        1, "stage table -> Jul 18-28 live numbers + input/funnelStart flags",
    ),
    (
        "Also carried in `booking_since_cutover`, deliberately NOT rendered as stages: "
        "`form_start_*` (25 events / 16 people), `completes_*` = client-side `booking_complete` (11 / 7), "
        "`portal_open_*` (5 / 3), `phone_click_*` (2 / 2), `lead_submit_*` (1 / 1), "
        "and `page_views` / `page_users` for `/book-beta` (87 views / 52 active users).",

        "Also carried in `booking_since_cutover`, deliberately NOT rendered as stages: "
        "`form_start_*` (26 events / 17 people), `completes_*` = client-side `booking_complete` (11 / 7), "
        "`portal_open_*` (7 / 4), `phone_click_*` (3 / 3), `lead_submit_*` (1 / 1), "
        "and `page_views` / `page_users` for `/book-beta` (98 views / 56 active users).\n\n"
        "**`booking_start` is a PAGEVIEW ALIAS, not an action.** It fires on page load and matches the "
        "`/book-beta` page counters *exactly* — 98 events against 98 `page_views`, 56 people against 56 "
        "`page_users` (GA4, 2026-07-29). Read it as \"reached the page\". It is never evidence that anyone "
        "began booking, and it carries no information the pageview does not already have. The 2026-07-25 "
        "note that \"booking_start can fire on page load\" is now confirmed, not suspected.",
        1, "non-stage keys -> live numbers + the pageview-alias finding",
    ),
    (
        "**`booking_complete_server` is AUTHORITATIVE for bookings.** The client-side `booking_complete` "
        "is suppressed by ad blockers and undercounts — 11 events / 7 people against 14 / 8 server-side "
        "over the same window. Never headline the client number.",

        "**`booking_complete_server` is AUTHORITATIVE for bookings.** The client-side `booking_complete` "
        "is suppressed by ad blockers and undercounts — 11 events / 7 people against 15 / 9 server-side "
        "over the same window. Never headline the client number.",
        1, "server-vs-client complete numbers -> live",
    ),
    (
        "**The click → page step is 1:1.** 49 tapped, 49 arrived. There is no click-to-page leak; "
        "do not go hunting for one. **The one real leak is 49 → 10 at \"picked a time\" (−80%).** "
        "Everything downstream holds (10 → 8 → 8). The renderer **computes** the worst step-over-step "
        "drop and highlights that row — it is never hardcoded, so it moves when the data moves.",

        "**There is no honest ratio between the tap row and the page row — they are not nested, and no "
        "ratio between them is honest in either direction.** This doc previously asserted \"the click → "
        "page step is 1:1, there is no click-to-page leak\" on the strength of 49 tapped / 49 arrived. "
        "That was a coincidence of two independent numbers passing each other, not a property of the "
        "funnel; by 2026-07-30 it read 53 tapped / 56 arrived and the dashboard was rendering **106% of "
        "the step before**. Two reasons the populations differ: `/book-beta` is reachable with **no tap "
        "at all** (the front-desk `?staff=1` bookmark, the Spruce confirmation SMS link, the GBP profile "
        "link, and typed or bookmarked URLs), and `people` = GA4 `totalUsers` = unique **browsers**, so a "
        "tap on a phone that finishes on a laptop is two users only one of whom tapped. The tap count can "
        "therefore legitimately exceed *or* trail the page count.\n\n"
        "**So: the tap row is an input signal (`input:true`) and the nested funnel starts at the page "
        "(`funnelStart:true`).** 53 people tapping is worth knowing and still renders, as a share of "
        "visitors. **The one real leak is 56 → 10 at \"picked a time\" (−82%).** Everything downstream "
        "holds (10 → 9 → 9). The renderer **computes** the worst step-over-step drop and highlights that "
        "row — never hardcoded, and it now ignores any drop measured off an input row. A computed "
        "conversion above 100% anywhere in the list renders \"not nested — exceeds the step above\" "
        "instead of a number: structure fixes today's case, the guard catches the next one.",
        1, "replace the 1:1 rule with the non-nested explanation",
    ),
    (
        "- **Reading:** 49 clicked → 49 arrived on the booking page (**1:1 — there is no click-to-page leak**) "
        "→ **10 picked a time (−80%, the one real leak)** → 8 entered details → 8 booked.",

        "- **Reading (as recorded 2026-07-28; the \"1:1\" conclusion was RETIRED 2026-07-30 — see §1):** "
        "49 clicked, 49 arrived on the booking page, → **10 picked a time (−80%, the one real leak)** → "
        "8 entered details → 8 booked. The 49/49 was a coincidence, not a 1:1 relationship: the two counts "
        "are different, non-nested populations and by 2026-07-30 read 53 / 56.",
        1, "section-4 ground-truth reading: annotate the retired 1:1 claim",
    ),
]


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found.", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if GUARD in s:
        print("ABORT: already patched. Nothing to do.")
        return 1

    bad = False
    for old, new, want, label in EDITS:
        got = s.count(old)
        if got != want:
            print(f"ABORT: '{label}' matched {got} time(s), expected {want}.",
                  file=sys.stderr)
            bad = True
    if bad:
        print("File not modified.", file=sys.stderr)
        return 3

    for old, new, want, label in EDITS:
        s = s.replace(old, new, want)
        print(f"  ok  {label}")

    bak = TARGET.with_name(f"DASHBOARD_METRICS.md.bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8", newline="")
    TARGET.write_text(s.replace("\n", "\r\n") if crlf else s,
                      encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}  (backup {bak.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
