# MBM Performance Dashboard — Metric Definitions, Refresh Mechanics, Failure Modes

**Last updated:** 2026-07-16 late (DATA_VERSION `2026-07-16e` — GBP Performance API wired, active-member + termination feed fields added)
**Artifact:** `C:\Users\charl\OneDrive\Documents\Claude\Artifacts\mbm-performance-dashboard\index.html`
**This doc is the contract.** Any task that refreshes, edits, or republishes the dashboard follows it. If you change a definition, change it here in the same commit.

---

## 0. Non-negotiables

1. **PHI:** aggregate counts only, ever. No names, no contact info, no patient lists, no screenshots of member views. Cowork and the Chrome extension are not under a BAA.
2. **Never bake a value you did not verify against its source this run.** If a pull fails, leave the old value AND its `as_of` untouched — the staleness chip is the honest signal. A fresh timestamp on stale data is lying.
3. **Windows are true month-to-date.** Never put a trailing-30-day number in an MTD slot (this inflated "July spend" 2.7× before the rebuild).
4. **Zero ≠ null.** A dead feed renders hatched/stale with its last-good value, never as 0.
5. **No trend arrows on counts below ~20/period.** One member = 33% swing = Poisson noise. Funnel rates render with Wilson 95% intervals.
6. **Check the artifact's `updatedAt` (list_artifacts) immediately before every publish.** Four publishes happened on 2026-07-16 alone; blind writes clobber.
7. **Version-bump discipline:** every publish bumps `DATA_VERSION` (date + letter). The seed only runs on a version change; a colliding version means your data silently never lands.
8. **Publish flow (learned 2026-07-16/17 the hard way):** a REMOTE publish (cloud session via update_artifact) STRIPS the artifact's mcpTools allowlist — the ↻ Refresh button dies until tools are re-declared locally. And the artifact's own Chat can hold a STALE copy — it once rolled the live artifact back to v16c by "re-declaring tools" from its cache. Rules: (a) publish from LOCAL tasks or the artifact Chat, feeding it a file on disk and verifying DATA_VERSION before it ships; (b) if a cloud session must publish, expect to re-declare the seven mcpTools afterward via the artifact Chat, from a disk file, never from its memory; (c) keep a known-good copy on disk (`dashboard_index_v2026-07-17a.html`, sha256 92b2e400…d58d) and update it whenever structure changes.

## 1. Metric definitions (label → exactly what it is)

### North Star
| Tile | Definition | Source | Window | Caveats |
|---|---|---|---|---|
| Active Concierge members | Count of memberships with status `active`, plan bucketed "concierge", excluding comps/F&F and test accounts. Goal: 300. | Hint via `export_dashboard_members.py` → `members_feed.json.active_members` (feed v2+); manual entry (Data Entry → Members) until first feed with the field | point-in-time | Feed upgrade committed 2026-07-16; populates on next poller run. Manual entries carry "manual entry · as of date". |
| New members (MTD) | Memberships **created** this month (`created_at`, NOT `start_date` which is the billing anchor) **with a payment method on file**. F&F comps and test accounts excluded. Split Concierge/SO. | Hint feed `members` / `members_split` | MTD | `members_pending` = created but no payment on file, shown separately, never summed in. `start_date` basis exported alongside as `members_anchored_by_start_date` for the renewals gap. |
| Booked consults (MTD) | `consults.booked_mtd_running_tally` — the erasure-proof tally `consult_count.py` persists daily. A consult = booking whose non-staff attendee is still a Contact (a real prospect). | Hint feed | MTD | Hint deletes the Contact attendee on enrollment, so in-window recounts UNDER-report; the running tally is authoritative. The old 30-min-slot heuristic over-read (14 vs 10 in July) and ships only as `consults_legacy_30min`. |
| Cost per new member / per booked consult | (Google Ads MTD spend + Nextdoor MTD spend) ÷ new members / booked consults. **Blended** — all consults/members regardless of channel. | computed | MTD | Meta spend excluded for the current month: its manual pull is a trailing-30 window and the campaign is frozen. Small-n: judge the 3-month direction. |

### Funnel (all MTD)
| Stage | Definition | Source |
|---|---|---|
| Site visits | `session_start` eventCount, split by sessionDefaultChannelGroup | GA4 Data API `runReport` |
| Booking + phone taps | `booking_click` + `phone_click` eventCount | GA4 |
| Reached Hint booking | `click` events with `linkDomain == mtbakermedical.hint.com` (outbound handoff — the closest thing to a booking-flow entry GA4 can see) | GA4 |
| Consults booked | running tally (above) | Hint |
| New paid members | paid new members (above) | Hint |

Deliberate exclusions: **no impressions band** (reach, not funnel — it dwarfs real stages into invisibility); **`form_start` removed everywhere** (the GA4 event was never implemented on the site — it can only ever read 0). The old North-Star "New leads" (taps bucketed by channel) is gone: taps are taps, and they're labeled as taps.

Consults are NOT a strict subset of handoffs (phone/walk-in bookings exist); members can close from prior-month consults. The funnel says so on-face.

### Attribution
"New members by lead source" = the self-reported Lead Source field in Hint, aggregate counts. **This is the primary attribution truth at MBM's volume** — last-click GA4 systematically undercredits GBP/referral/word-of-mouth across a ~3-week, 5-visit consideration cycle ending off-domain. The quarterly rollup of this bar is the real channel report. GA4 channel splits are directional decoration by comparison. Gap as of 2026-07: 0 of 3 July members have Lead Source set in Hint — fix at signup intake.

### Paid
| Tile | Definition | Caveats |
|---|---|---|
| Google Ads spend/clicks/CPC | GAQL v21, campaign resource, **true MTD** | CPC ~$3–4, not the folkloric $2 |
| Ad-attributed taps | GA4-imported booking_click+phone_click from **`metrics.all_conversions`** | Immune to the primary/secondary goal misconfig that zeroed `metrics.conversions` Jul 2–16. Campaign-table "Conv*" column = primary conversions, annotated. |
| GBP local actions | Google-hosted conversion actions (calls, directions, website visits, other) from Ads | **Ad-driven only.** Engagement on Google surfaces, 24–48h lag, click-to-call undercounts real calls. **Never summed with site conversions** (double-counting). |
| GBP profile actions | Business Profile Performance API: CALL_CLICKS, WEBSITE_CLICKS, BUSINESS_DIRECTION_REQUESTS, daily | **All-source** (organic + maps + ads) — broader than local actions. Reached via the **Zapier GBP connector's raw GET** (its OAuth carries business.manage — no separate Google API approval needed). Location ID `3763111265482720361` (from the business.google.com URL `#mpd=~<id>`). Google lags 2–3 days; missing days in the response = 0. Zapier CENSORS ids in account-listing responses, but a URL you construct yourself works fine. Pre-category-change baseline: June = 13 calls / 29 site clicks / 67 directions; Jul 1–15 = 11 / 11 / 51 (Jul 14: anomalous 22 directions — investigate before crediting the category change). |
| Meta | Manual weekly Ads Manager pull. "Results" = **landing-page views, not leads** (no lead event on pixel) | Campaign flagged "Not delivering — Ad errors" since ~Jul 9; numbers frozen. |
| Nextdoor | Manual weekly pull; spend/impr/clicks only (no pixel) | — |

### SEO / GBP
- GSC: latest **complete** month is the KPI row; current month appears only as a dashed projection (counts scaled by days_in_month/settled_days; CTR and position deliberately NOT scaled — they're rates). GSC settles 2–3 days late.
- Ahrefs DR ~1 is correct, not a bug: ~280 referring domains, ~7 dofollow, rest nofollow citations. More citations will never move DR.
- GBP: category experiment (Family practice physician → Medical clinic, changed 2026-07-16, readout 2026-08-06, first signal 1–2 wks, stable 3–4 wks). Pack positions are manual SERP reads — proximity-personalized, ±1 is noise. GBP Performance API blocked on Google API approval; interim engagement = Ads local actions.

## 2. Refresh mechanics (who writes what, when)

```
9:15a  MBM-Dashboard-Members-Export (Windows task, laptop)
       └─ export_dashboard_members.py → members_feed.json  (Hint aggregates:
          active_members [v2+], members, pending, consults running tally, warnings)
9:30a  mbm-dashboard-daily-fast (Cowork task)
       └─ reads members_feed.json (incl. active_members → SNAPSHOT.members.active,
          terminations_mtd, consults.booked_mtd_running_tally)
          + GA4 (runReport MTD: events by channel, click-by-linkDomain handoffs)
          + Google Ads (GAQL v21 MTD; 'conversions' = GA4-imported all_conversions)
          + GBP (Performance API via Zapier raw GET, location 3763111265482720361:
            CALL_CLICKS / WEBSITE_CLICKS / BUSINESS_DIRECTION_REQUESTS, MTD → gbp_perf)
          → updates SNAPSHOT block in index.html, sets sources.*.as_of per source
          actually refreshed, bumps DATA_VERSION, checks updatedAt, republishes
Mon    mbm-dashboard-weekly-refresh (Cowork task)
       └─ adds GSC (browser/API), Ahrefs (browser if API units exhausted),
          Meta + Nextdoor Ads Manager pulls (Chrome), KW re-bake
7:15a  mbm-dashboard-health-check (remote scheduled task, cloud)
       └─ READ-ONLY: GA4 daily taps + Ads daily conversions + artifact updatedAt;
          alerts Charlie (push/email) on blackout/staleness. Never edits the artifact.
On-open  ↻ Refresh live (in-page, optional)
       └─ window.cowork.callMcpTool → Zapier GA4/Ads + Ahrefs MCP. Per-source:
          success updates src_asof:<k>; failure sets src_err:<k> → red chip.
          Retries once with backoff. NOTHING falls back silently.
```

**The SNAPSHOT contract (in index.html):** all baked data lives in one `SNAPSHOT` const with a `sources` block (`as_of`, `cadence_h` per source). A refresh task updates the data, sets that source's `as_of`, bumps `DATA_VERSION`, republishes. Freshness chips and stale-hatching are computed at render from `as_of` vs `cadence_h` — set `as_of` ONLY for sources you actually refreshed.

**API rules (hard-won):**
- Google Ads: **API v21 only** (v17/v18 → 404, v20 → blocked). `searchStream` endpoint. **NEVER `create_report`** — it silently overrides your field list with an LLM guess.
- GA4: raw Data API via Zapier `_zap_raw_request` POST to `properties/513547844:runReport`. The packaged runReport action is unreliable. No raw GET exists on this connector (use the Data API's POST-everything surface).
- Ahrefs: native MCP (`mcp__251f7265…`), NOT Zapier. Units-limited — exhausted as of 2026-07-16; UI is units-free. Empty result arrays (`metrics: []`) are a FAILURE state, not "no change" — rank-tracker with `date: today` can return empty when no snapshot exists for that date.
- Hint: laptop pollers only. Keys never leave the laptop.

## 3. Failure modes & the alerting model

| Failure | Detection | Behavior |
|---|---|---|
| Source pull fails (Zapier "MCP server connection lost", API error) | per-source try/catch with 1 retry + backoff | last-good value stays, chip turns red with error text; tile hatches when age > 2× cadence. Page never blanks; failures are per-metric. |
| Poller misses runs | hint `as_of` age > 26h (amber) / > 52h (red) | member/consult tiles hatch; alert names the task (MBM-Dashboard-Members-Export) |
| Ads conversion pipeline breaks | **7-day window: primary conversions = 0 while taps ≥ 10 and paid clicks ≥ 30** → red alert | this exact detector would have fired 2026-07-03 during the July incident |
| Site tagging breaks | ≥2 consecutive zero-tap days (p<1% at ~4 taps/day) | red alert: "tracking broken, not demand" |
| Consult tally stale | feed warning when `consult_count_state.json` ≥2 days old | surfaced in feed `warnings` |
| Refresh task dies entirely | remote health-check task (cloud, independent of laptop AND of the dashboard being opened) pushes/emails | the dashboard can't alert you if nothing opens it — the health check can |

**Incident log — July 2026 "conversion blackout" (resolved):** Google Ads `Conversions` column read 0 from Jul 2–16 while the actions showed ENABLED. Root cause was NOT a dead GA4 link: around Jul 1 the GA4-imported `booking_click`/`phone_click` actions were demoted from **primary** to secondary (`primary_for_goal=false`), and the only primary web goal left was "Sign-up (signup_complete)" — a GA4 event that does not exist on the site. Real conversions kept flowing into `all_conversions` the whole time (they reconcile with GA4 paid-search taps). **Fixed 2026-07-16 via Google Ads API:** both actions restored to primary (conversionActions 7635222383, 7635222386). Residual: implement `signup_complete` in GA4 or demote that action; watch that smart bidding re-learns over ~1–2 weeks. Lesson: dashboards must read `all_conversions` for GA4-imported actions and alert on primary-column silence.

## 4. Known gaps / status (updated 2026-07-16 late)
- **Active-member counts**: `active_members` (whole-book, status=='active', F&F/test excluded) now in `export_dashboard_members.py`; dashboard prefers the feed-baked `SNAPSHOT.members.active` over manual entry once the refresh task bakes it. Verify `status_histogram` on first run — if Hint uses a status other than `active`, adjust the filter.
- **Terminations**: `terminations_mtd` field added (end-date-in-month basis, defensive). If `end_field` comes back null with `end_status_without_date > 0`, extend `_END_KEYS` in the exporter. Presentation rule: net growth = adds − terms; at ~150 members show TTM churn + cohorts, never monthly churn %.
- **Lead Source discipline**: ask "how did you hear about us" at signup, record in Hint. 0/3 July members have it. The only durable attribution.
- **Hint booking-flow visibility**: asked Hint support (in-app chat, 2026-07-16) whether the hosted signup allows a GA4/GTM tag or UTM passthrough; custom-signup API is the fallback. Handoff stage remains the proxy meanwhile. NOTE: Hint also has an open reply waiting on Charlie in the "New phone number setup" thread (they asked which area code).
- **GBP Performance API**: ✅ SOLVED 2026-07-16 via Zapier raw GET (no Google approval needed). Baked into the daily refresh contract (§2).
- **Meta**: diagnosed 2026-07-16 — the "Not delivering" campaign's ad is **Rejected** ("doesn't comply with our Advertising Policies", generic policy strike; likely personal-health policy given the creative). Untouched per Charlie. Options: edit creative + resubmit, request re-review via Business Support Home, or pause. Also pending in Ads Manager: "New Leads Ad" in draft, 3 unpublished changes ("Review and publish (3)"), and a **"Verification may be required soon"** banner — identity verification is Charlie-only, do not automate.
- **Google Ads goal config**: booking_click + phone_click restored to primary 2026-07-16; "Sign-up (signup_complete)" **demoted to secondary** 2026-07-16 (the GA4 event doesn't exist — re-promote only after the event is actually implemented). Watch: Google search-panel showed "Branded | Bellingham | Search — NOT ELIGIBLE, your ads aren't showing" on 2026-07-16 — check the campaign's status/budget/policy in Ads if it persists.
- **Ahrefs units**: exhausted 2026-07-16; SEO live-refresh effectively disabled until reset. Weekly browser read is the fallback.
- **GBP category on SERP**: knowledge panel still showed "Family practice physician" on 2026-07-16 evening — propagation lag, recheck in 24–48h.
