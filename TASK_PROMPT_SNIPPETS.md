# Dashboard scheduled-task prompts (FULL REPLACEMENTS, 2026-07-17)
(Charlie: paste each block wholesale over the matching Cowork task's prompt.
Supersedes the 2026-07-16 snippets below the fold.)

## mbm-dashboard-daily-fast (9:30 AM daily)

```
You refresh the MBM performance dashboard every morning. CONTRACT: follow
mbm-review-receiver/DASHBOARD_METRICS.md exactly - it is the authority; if this
prompt and the doc disagree, the doc wins. PHI: aggregate counts only, ever - no
names, no patient lists. All baked data lives in the SNAPSHOT const of
mbm-review-receiver/dashboard_index.html (the ONE canonical file). Update only
values you verified against their source THIS run; set sources.<key>.as_of ONLY
for sources you actually refreshed. If a pull fails after one retry, leave the old
value AND its as_of untouched and move on - the staleness chip is the signal.
Never fake a timestamp on stale data. Zero != null.

1. HINT FEED: read members_feed.json (sibling of the dashboard artifact,
   C:\Users\charl\OneDrive\Documents\Claude\Artifacts\mbm-performance-dashboard\).
   If feed.active_members is present: SNAPSHOT.members.active = {concierge, so,
   as_of: feed.generated_at, source:"hint"}. Copy the reconciliation grid:
   SNAPSHOT.members.active_recon = {unpaid_concierge:
   reconciliation_by_status.unpaid.concierge||0, pending_concierge:
   reconciliation_by_status.pending.concierge||0, comp_active:
   friends_family_active_excluded, test_active_excluded, as_of: feed.generated_at}.
   Bake feed.terminations_mtd. Consults = feed.consults.booked_mtd_running_tally,
   NEVER the 30-min heuristic. New members = feed.members / members_split /
   members_pending (created_at basis, payment on file). Surface any feed.warnings[]
   to Charlie in your run summary. If the feed file's generated_at is older than
   24h, do NOT bake it - report that the 9:15 exporter didn't run.

2. GA4 (Data API runReport, true MTD): visits + booking/phone tap EVENTS by
   channel; outbound linkDomain clicks (handoffs - instrumentation only); deduped
   PEOPLE via totalUsers with the booking-OR-phone orGroup filter ->
   SNAPSHOT.funnel_mtd {visits, taps_booking, taps_phone, handoffs, visits_src,
   taps_src, users:{tappers, handoff, booking_channel, as_of}}. Update
   SNAPSHOT.channel_econ rows (spend + taps + chan_people per channel; windows
   labeled honestly - never mix trailing windows into MTD).

3. EXTEND SNAPSHOT.daily ARRAYS THROUGH YESTERDAY - EVERY RUN, not weekly:
   dates, taps, handoffs, ads_clicks, ads_conv_primary, ads_conv_ga4_all. The
   anomaly detectors read these; QA fails any bake whose last date lags baked_at
   by >2 days.

4. GOOGLE ADS: GAQL v21 searchStream ONLY (never create_report), true MTD ->
   SNAPSHOT.gads.mtd {spend, impressions, clicks, conv_primary, conv_ga4_all}
   and campaigns_mtd (rows must sum to the MTD spend total). "conversions" =
   GA4-imported booking_click+phone_click from metrics.all_conversions, NOT the
   primary column. Also local_actions_mtd.

5. LSA (same Ads account): (a) MTD -> SNAPSHOT.lsa {leads_phone_mtd,
   leads_message_mtd, leads_charged_mtd, leads_credited_mtd, spend_mtd} via
   local_services_lead GAQL, creation_date_time MTD. (b) LSA TEST - cumulative,
   NEVER reset at month rollover: SNAPSHOT.lsa.test.leads_charged_cum =
   charged leads with creation_date_time >= 2026-07-17 (disputed/credited
   excluded via lead_status + credit_details.credit_state); spend_cum =
   LOCAL_SERVICES campaign cost, segments.date >= 2026-07-17;
   patients_attributed + attributed_pending from feed.lsa_test. Feed missing
   lsa_test -> leave existing values, never bake 0. The tile's decision-rule text
   is static config - do not touch it.

6. GBP PROFILE ACTIONS: Zapier GBP connector raw GET, one call per metric,
   location 3763111265482720361, MTD:
   https://businessprofileperformance.googleapis.com/v1/locations/3763111265482720361:getDailyMetricsTimeSeries?dailyMetric=CALL_CLICKS&dailyRange.start_date.year=YYYY&dailyRange.start_date.month=M&dailyRange.start_date.day=1&dailyRange.end_date.year=YYYY&dailyRange.end_date.month=M&dailyRange.end_date.day=DD
   (repeat for WEBSITE_CLICKS and BUSINESS_DIRECTION_REQUESTS). Days with no
   "value" = 0. Write SNAPSHOT.gbp_perf {calls, website_clicks,
   direction_requests, window, as_of}; keep june_baseline. Google lags 2-3 days -
   lag, not decline.

6b. FINANCE (profitability widget, TOP of dashboard):
   - COSTS (QuickBooks): call the QuickBooks connector's company_info tool FIRST
     (opens the OAuth session), THEN profit_loss_quickbooks_account with
     periodStart = current month's 1st (YYYY-MM-01) and periodEnd = today. Take the
     top-level totalExpenses -> SNAPSHOT.finance.costs.mtd; set finance.costs.as_of
     AND sources.qb.as_of to now (ISO). QB income is ~$0 because concierge revenue
     is collected in Hint, not booked in QuickBooks - take ONLY totalExpenses, IGNORE
     QB's own net/income. Connector dead/unauthorized -> leave finance.costs.mtd +
     sources.qb.as_of untouched and REPORT; never bake 0 or a guessed number.
   - REVENUE (Hint): copy feed.revenue_mtd.collected -> SNAPSHOT.finance.revenue.mtd
     (as_of = feed.generated_at). Surface any feed revenue_mtd warning (especially
     the cents-vs-dollars one) to Charlie. Feed missing revenue_mtd -> leave null +
     report.
   - Net + margin are computed at RENDER time when BOTH sides are present (Hint
     revenue - QB costs); if either is null the widget shows "-". Never compute or
     hardcode net yourself.
6c. KIT SIGNUPS: via the Kit connector (v4 API), count perimenopause-checklist form
   subscribers created this month -> SNAPSHOT.kit.checklist_signups_mtd, plus total
   list size -> SNAPSHOT.kit.list_total, sources.kit.as_of = now. Unauthorized ->
   nulls + report.

7. BAKE + PUBLISH: set baked_at; bump DATA_VERSION to the next date+letter
   (check the file's current version first - other sessions bump it too; a
   collision means data silently never lands). WRITE RULES (doc 0.8): write
   dashboard_index.html by write-then-append chunks <=60KB - NO single-shot
   write at ANY size - then verify: byte count, sha256, ends </html>,
   node --check on the inline JS, 3/3 script tags. Check the artifact's
   updatedAt (list_artifacts) immediately before publishing. Publish from the
   disk file, verify DATA_VERSION + sha match what you just wrote, confirm the
   mcpTools allowlist survives in the published copy. Beware stale mounts (doc
   0.9d/e): verify against a source that independently agrees (e.g. Grep + bash)
   before trusting a sha.

8. REPORT to Charlie: version published, what refreshed, what failed and was
   left stale, any feed warnings, and any alert that newly fired (blackout
   detector, zero-tap run, unpaid-concierge billing alert).
```

## mbm-dashboard-weekly-refresh (Mon 9:15 AM)

```
You do the weekly deep refresh of the MBM performance dashboard. Run the ENTIRE
daily contract first (it is the prompt of mbm-dashboard-daily-fast; the doc
mbm-review-receiver/DASHBOARD_METRICS.md is the authority), then add:

W1. GSC: latest COMPLETE month is the KPI row; current month only as the dashed
    projection (counts scaled by days_in_month/settled_days; CTR and position
    NOT scaled - they're rates). GSC settles 2-3 days late. Small-multiples
    charts, never dual-axis.

W2. AHREFS: refresh DR / referring domains. Empty API responses are a FAILURE,
    not a result (throw, keep last-good + stale chip); if API units are
    exhausted, do a browser read and label it. REGENERATE the dashboard's
    tracked-keyword list from the Rank Tracker itself - do not trust the
    baked list (it drifted once: five "dead" keywords were never tracked).
    Keyword allowance is 42, not 50.

W3. META + NEXTDOOR: weekly Ads Manager pulls. Meta "Results" = landing-page
    views, NEVER label them leads (no lead event on the pixel). Label windows
    honestly (Meta is trailing-30, not MTD - keep it excluded from MTD spend
    ratios). Nextdoor: spend/impressions/clicks only, no pixel. If Nextdoor ads
    cleared review this week, confirm the utm_source=nextdoor tags are live and
    note whether GA4 sessionMedium shows nextdoor traffic yet.

W4. GBP ATTRIBUTION + EXPERIMENT: GA4 sessionMedium=gbp -> SNAPSHOT.gbp_attrib
    {sessions, users, taps, handoffs}. Do NOT touch the category-experiment
    block or baselines; the 2026-08-06 readout is manual. Before crediting the
    category change with anything, check the Jul-14 anomalous 22 direction
    requests note.

W5. HOUSEKEEPING: surface in your report anything in feed.warnings[] that has
    persisted more than a week (e.g. "Lead Source not API-exposed"), any source
    whose as_of is older than 2x its cadence, and whether the LSA test tile's
    checkpoint (12 billed leads) has been reached - if yes, tell Charlie to
    apply the decision rule; never auto-decide it.

Same bake/publish/report rules as the daily task (doc 0.7-0.9): version bump
with collision check, chunked writes with sha verification, updatedAt check
before publish, publish from disk, report what refreshed / what's stale / what
fired.
```
