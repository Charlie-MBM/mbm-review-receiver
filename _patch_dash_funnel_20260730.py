#!/usr/bin/env python3
"""Dashboard funnel: stop rendering a funnel that goes UP, plus two label-drift fixes.

Run from mbm-review-receiver:  py _patch_dash_funnel_20260730.py

THE BUG (live on the dashboard right now, found 2026-07-30):
The people-first funnel renders these two rows consecutively and divides one by
the other:

    Tapped "Book a free consult"   booking_click   53 people
    Reached the booking page       booking_start   56 people

56 / 53 = 106%, so the dashboard prints "106% of the step before" on a funnel
row. The doc and the on-page copy both still assert "1:1, there is no
click-to-page leak" from when the numbers were 49 / 49.

AND 56 > 53 IS NOT A DATA ERROR. The two rows measure different populations, not
nested ones:
  * People reach /book-beta without ever tapping a CTA — the front-desk bookmark
    (?staff=1), the Spruce confirmation link, the GBP profile link, and typed or
    bookmarked URLs.
  * people = GA4 totalUsers = unique BROWSERS. A tap on a phone that finishes on
    a laptop is two users, one of whom never tapped.
So the tap count can legitimately exceed OR trail the page count, and no
percentage between them is honest.

Separately verified while chasing this: booking_start is a PAGEVIEW ALIAS. It
matches the /book-beta page counters exactly — 98 starts_events vs 98 page_views,
56 starts_people vs 56 page_users — because it fires on load. It is "reached the
page", never "started booking", and it carries zero information the pageview
doesn't already have. That is recorded in a comment so nobody reads it as intent.

THE FIX, three layers:
  1. STRUCTURAL — the tap row is flagged `input:true` (a signal, not a stage) and
     the page row is flagged `funnelStart:true`. The nested funnel starts at the
     page: 56 -> 10 -> 9 -> 9, all genuinely nested. The tap row still renders
     with its share of visitors, because 53 people tapping is worth knowing.
  2. GUARD — anywhere in the list, a computed conversion above 100% now renders
     "not nested — exceeds the step above" instead of a number. Structure fixes
     today's case; the guard catches the next one.
  3. HONEST COPY — the leak box's hardcoded "1:1, there is no click-to-page leak"
     is replaced by computed text that states both numbers and, when the page
     count exceeds the tap count, explains why in one sentence.

ALSO FIXED (both are hardcoded labels that drifted off their own data):
  * "(10 days since the /book-beta cutover)" — the window is now Jul 18-28, i.e.
    11 days. Day count is computed from window_start/window_end.
  * GBP engagement line says "Jul 1-15 = 17 / 38 / 62" while gbp_perf.window says
    "Jul 1-28". The numbers are the Jul 1-28 ones; only the label was stale. Now
    reads gbp_perf.window.
  * GBP taps were stored in two places and drifted (gbp_attrib.taps = 4, as_of
    07-27; channel_econ "GBP (profile link)" row taps = 6, as_of 07-29 — same
    measure, only the table gets refreshed). The note now derives taps from the
    channel_econ row and shows that row's as_of.

NOT A BUG, checked and left alone: gads.mtd.clicks 302 vs sum(daily.ads_clicks)
300. The daily array is Jul 1-28 and sums to exactly 300; the five campaign rows
sum to exactly 302; the MTD pull ran 2026-07-29T16:41Z and includes 2 clicks from
Jul 29 so far. Different end dates, both correct. Documented in
DASHBOARD_METRICS.md rather than changed here.

Count-asserted; aborts before writing on any mismatch. Backs up the file.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "dashboard_index.html"
STAMP = "20260730-funnel"

EDITS = [
    # ---------------------------------------------------------------- 1. bcDays
    (
        '''  const bc=SNAPSHOT.funnel_mtd.booking_since_cutover;
  const st=[''',
        '''  const bc=SNAPSHOT.funnel_mtd.booking_since_cutover;
  /* Window length is COMPUTED, never typed. The caption read "10 days" against a
     Jul 18-28 window on 2026-07-30 because it was hardcoded when the window ended
     on the 27th. */
  const bcDays=(function(){
    const a=Date.parse(bc.window_start+"T00:00:00Z"), b=Date.parse(bc.window_end+"T00:00:00Z");
    return (isFinite(a)&&isFinite(b))?Math.round((b-a)/86400000)+1:null;
  })();
  const st=[''',
        1, "compute bcDays from the window dates",
    ),
    # ------------------------------------------------------- 2. stage flags
    (
        '''    {lab:'Tapped "Book a free consult"', ev:"booking_click",           people:bc.clicks_people,           events:bc.clicks_events},
    {lab:"Reached the booking page",     ev:"booking_start",           people:bc.starts_people,           events:bc.starts_events},''',
        '''    /* input:true -- a SIGNAL, not a nested stage. Taps and page-arrivals are
       different populations: the front-desk bookmark (?staff=1), the Spruce
       confirmation link, the GBP profile link and typed/bookmarked URLs all land
       on /book-beta with no tap, and people = unique BROWSERS, so a tap on a phone
       that finishes on a laptop is two users only one of whom tapped. On
       2026-07-30 this row read 53 against the page row's 56 and the funnel
       rendered "106% of the step before". No ratio between these two is honest. */
    {lab:'Tapped "Book a free consult"', ev:"booking_click",           people:bc.clicks_people,           events:bc.clicks_events, input:true},
    /* funnelStart:true -- the nested funnel begins HERE, not at the tap.
       booking_start is a PAGEVIEW ALIAS, not an action: it fires on load and
       matches the /book-beta page counters exactly (98 events vs 98 page_views,
       56 people vs 56 page_users, GA4 2026-07-29). Read it as "reached the page";
       it is never evidence anyone began booking. */
    {lab:"Reached the booking page",     ev:"booking_start",           people:bc.starts_people,           events:bc.starts_events, funnelStart:true},''',
        1, "flag the tap row as input and the page row as funnelStart",
    ),
    # ------------------------------------------------------- 3. conv loop
    (
        '''  let worst=-1, worstDrop=null;
  for(let i=1;i<st.length;i++){
    const prev=st[i-1].people, cur=st[i].people;
    if(prev>0 && cur!=null){
      st[i].conv=(cur/prev)*100;
      const drop=100-st[i].conv;
      if(!st[i-1].reach && drop>0 && (worstDrop==null || drop>worstDrop)){ worstDrop=drop; worst=i; }
    }
  }''',
        '''  let worst=-1, worstDrop=null;
  for(let i=1;i<st.length;i++){
    /* The row that STARTS the nested funnel has no honest "of the step before":
       the row above it is an input signal over a different population, not a
       superset of it. */
    if(st[i].funnelStart) continue;
    const prev=st[i-1].people, cur=st[i].people;
    if(prev>0 && cur!=null){
      const conv=(cur/prev)*100;
      /* Guard for this class of bug anywhere else in the list. If a stage exceeds
         the one above it the two are not nested, and printing 106% would assert a
         funnel that grows. Say what is actually true instead. */
      if(conv>100){ st[i].notNested=true; continue; }
      st[i].conv=conv;
      const drop=100-conv;
      if(!st[i-1].reach && !st[i-1].input && drop>0 && (worstDrop==null || drop>worstDrop)){ worstDrop=drop; worst=i; }
    }
  }''',
        1, "conv loop: skip funnelStart, refuse >100%, don't measure drops off an input row",
    ),
    # ------------------------------------------------------- 4. conv cell render
    (
        '''      '<div class="vconv">'+(s.conv==null
        ? '<span class="vstart">'+(s.reach?'reach — everyone who visited':'start of funnel')+'</span>' '''.rstrip(),
        '''      '<div class="vconv">'+(s.conv==null
        ? '<span class="vstart">'+(s.reach?'reach — everyone who visited'
            :s.notNested?'not nested — exceeds the step above'
            :s.funnelStart?'start of the booking flow'
            :s.input?'input signal, not a stage'
            :'start of funnel')+'</span>' '''.rstrip(),
        1, "render the input / funnelStart / notNested cases",
    ),
    # ------------------------------------------------------- 5. window caption
    (
        '''escapeHtml(bc.window_end)+' (10 days since the /book-beta cutover) · GA4, as of '+escapeHtml(bc.as_of)''',
        '''escapeHtml(bc.window_end)+(bcDays?' ('+bcDays+' days since the /book-beta cutover)':' (since the /book-beta cutover)')+' · GA4, as of '+escapeHtml(bc.as_of)''',
        1, "computed day count in the window caption",
    ),
    # ------------------------------------------------------- 6. leak box copy
    (
        '''      leakHtml='Since the <b>2026-07-18 cutover</b> booking completes on our own site (/book-beta), so GA4 sees the whole flow. Over the first 10 days (Jul 18–27): <b>'+fmtN(bk.clicks_people)+'</b> people tapped a booking CTA and <b>'+fmtN(bk.starts_people)+'</b> reached the booking page — <b>1:1, there is no click-to-page leak</b> — and <b>'+fmtN(bk.completes_server_people)+'</b> came out the far end booked (server-confirmed).'+''',
        '''      const bkDays=(function(){
        const a=Date.parse(bk.window_start+"T00:00:00Z"), b=Date.parse(bk.window_end+"T00:00:00Z");
        return (isFinite(a)&&isFinite(b))?Math.round((b-a)/86400000)+1:null;
      })();
      /* The old copy asserted "1:1, there is no click-to-page leak" — true when
         both numbers were 49, false and unfixable as a claim once they diverged.
         Both numbers are now stated and the gap is explained where it exists. */
      const pageOverTap=(bk.clicks_people!=null&&bk.starts_people!=null)?(bk.starts_people-bk.clicks_people):null;
      leakHtml='Since the <b>2026-07-18 cutover</b> booking completes on our own site (/book-beta), so GA4 sees the whole flow. Over '+(bkDays?'the first <b>'+bkDays+'</b> days':'the window')+' ('+escapeHtml(bk.window_start)+' → '+escapeHtml(bk.window_end)+'): <b>'+fmtN(bk.clicks_people)+'</b> people tapped a booking CTA, <b>'+fmtN(bk.starts_people)+'</b> reached the booking page, and <b>'+fmtN(bk.completes_server_people)+'</b> came out the far end booked (server-confirmed).'+
        (pageOverTap!=null&&pageOverTap>0
          ? '<br><span class="small">More people reached the page ('+fmtN(bk.starts_people)+') than tapped a CTA ('+fmtN(bk.clicks_people)+'), which is expected rather than an error: the front-desk bookmark, the Spruce confirmation link, the GBP profile link and typed or bookmarked URLs all land on /book-beta without a tap, and a tap on a phone that finishes on a laptop counts as two browsers. Taps are an input signal, so the funnel starts at the page, not at the tap.</span>'
          : '')+''',
        1, "leak box: drop the hardcoded 1:1 claim, explain the gap",
    ),
    # ------------------------------------------------------- 7. GBP taps source of truth
    (
        '''  document.getElementById("gbpNote").innerHTML =
    '<b>The bet:</b> ''',
        '''  /* GBP taps were stored TWICE — SNAPSHOT.gbp_attrib.taps and the channel_econ
     "GBP (profile link)" row — and drifted (4 as of 07-27 vs 6 as of 07-29) because
     the daily refresh rewrites the channel table and not gbp_attrib. Same measure,
     so read one source: the table, falling back to the stored value only if the row
     is gone. */
  const gbpRow=((SNAPSHOT.channel_econ&&SNAPSHOT.channel_econ.rows)||[]).filter(function(r){return /^GBP/.test(r.ch||"");})[0]||null;
  const gbpTaps=(gbpRow&&gbpRow.taps!=null)?gbpRow.taps:(SNAPSHOT.gbp_attrib?SNAPSHOT.gbp_attrib.taps:null);
  const gbpTapsAsOf=(gbpRow&&gbpRow.taps!=null)?((SNAPSHOT.channel_econ&&SNAPSHOT.channel_econ.as_of)||''):((SNAPSHOT.gbp_attrib&&SNAPSHOT.gbp_attrib.as_of)||'');
  document.getElementById("gbpNote").innerHTML =
    '<b>The bet:</b> ''',
        1, "derive GBP taps from the channel_econ row",
    ),
    (
        '''fmtN(SNAPSHOT.gbp_attrib.taps)+' booking/phone taps · '+fmtN(SNAPSHOT.gbp_attrib.handoffs)+' reached Hint booking <span class="small">(as of '+escapeHtml(SNAPSHOT.gbp_attrib.as_of||'')+' · undercounts:''',
        '''fmtN(gbpTaps)+' booking/phone taps · '+fmtN(SNAPSHOT.gbp_attrib.handoffs)+' reached Hint booking <span class="small">(sessions as of '+escapeHtml(SNAPSHOT.gbp_attrib.as_of||'')+', taps as of '+escapeHtml(gbpTapsAsOf)+' · undercounts:''',
        1, "render the derived GBP taps with its own as_of",
    ),
    # ------------------------------------------------------- 8. gbp_perf window label
    #
    # The rendered line labels gbp_perf's numbers "Jul 1-15" while gbp_perf.window
    # says "Jul 1-28" and the values (17 / 38 / 62) are the Jul 1-28 ones. Only the
    # label is stale, so read the window off the data.
    # NOTE: the "Jul 1-15 = 11 / 11 / 51" inside the comment block above
    # SNAPSHOT.gbp_perf is NOT this bug -- that is the genuine pre-category-change
    # partial-month baseline and is correct as written. Left alone deliberately.
    (
        ''' directions; Jul 1–15 = '+fmtN(SNAPSHOT.gbp_perf.calls)+''',
        ''' directions; '+escapeHtml(SNAPSHOT.gbp_perf.window||'current window')+' = '+fmtN(SNAPSHOT.gbp_perf.calls)+''',
        1, "GBP engagement label reads gbp_perf.window instead of a stale Jul 1-15",
    ),
    # ------------------------------------------------- 9. static funnel prose
    #
    # The section note under the Booking Funnel heading hardcodes BOTH the day count
    # and the window ("the 10 days ... (Jul 18-27)") in static HTML, next to a pill
    # that renders the same window from the data. Two copies, one of them stale.
    # Delete the copy rather than trying to keep it in sync.
    (
        '''The window is the 10 days since the 2026-07-18 <b>/book-beta</b> cutover (Jul 18–27), the only period in which GA4 sees the flow end to end;''',
        '''The window is the period since the 2026-07-18 <b>/book-beta</b> cutover shown in the pill above — the only period in which GA4 sees the flow end to end;''',
        1, "funnel section note: stop hardcoding the window in prose",
    ),
]

GUARD = "funnelStart:true"


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found.", file=sys.stderr)
        print("Run this from mbm-review-receiver.", file=sys.stderr)
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

    # The whole point of the patch is that no ratio is taken between the tap row
    # and the page row. If the old unguarded division survived anywhere, stop.
    if "st[i].conv=(cur/prev)*100;" in s:
        print("ABORT: the unguarded conversion division is still present. "
              "File not modified.", file=sys.stderr)
        return 4

    bak = TARGET.with_name(f"dashboard_index.html.bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8", newline="")
    TARGET.write_text(s.replace("\n", "\r\n") if crlf else s,
                      encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}  (backup {bak.name})")
    print("Open it in a browser and check the funnel: the page row should read")
    print("\"start of the booking flow\", and no row should show a percentage")
    print("above 100.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
