# MBM Performance Dashboard — Metric Definitions, Refresh Mechanics, Failure Modes

**Last updated:** 2026-07-19 (DATA_VERSION `2026-07-18c` — taller GSC clicks/impressions charts + per-chart current-month pace line; `2026-07-18b`/`2026-07-18a` daily bakes; **hosted partner copy added**: private Cloudflare Worker `mbm-dashboard` serving the baked HTML at an unguessable noindex URL, pushed each bake via `push_hosted_dashboard.py` — see §2. Prior: `2026-07-17m` — badge reconciliation + unpaid-concierge billing alert + manual-entry timestamp fix; `2026-07-17l` first task-baked feed w/ active members; `2026-07-17k` funnel label-clip fix; `2026-07-17j` — "LSA test" kill/keep tracker tile added: `SNAPSHOT.lsa.test` cumulative-since-2026-07-17 fields, decision rule locked, exporter `google_lsa` lead-source bucket + `lsa_test` feed key. Prior same-day: `2026-07-17i` funnel restructure — tap→Hint arrival ≈100%, handoff demoted to instrumentation; `2026-07-17h` LSA added as first pay-per-lead paid source: `SNAPSHOT.lsa` + `sources.lsa`, Paid tile, channel-econ row, `LSA_GO_LIVE` gate; campaign PAUSED, verified zeros)
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
8. **The write ceiling is NOT a fixed 97KB — treat EVERY single-shot device write as unsafe (learned 2026-07-17, three times):** single-shot file writes to the device — Cowork task writers AND the cloud device_commit_files path alike — silently truncate. First observed around 97,000 bytes (v17e/v17f), but on 2026-07-17 a 24KB markdown file and a 43KB python file BOTH truncated at ~20–27KB on the same path. The ceiling is variable; no size is provably safe. v17e and v17f both shipped to disk truncated (dead `init()`, no `</html>`) while the writer reported success. `dashboard_index.html` is now >100KB and will only grow. RULES: (a) never single-write the dashboard file — write it in chunks ≤60KB and concatenate on the device (`cat chunk_00 chunk_01 > dashboard_index.html` in the mounted repo, or write-then-append for task writers); (b) ALWAYS self-verify after writing: byte count matches the source, `sha256sum` matches, file ends `</html>`, `node --check` passes on the inline JS — a writer that reports success without verifying will eventually publish a broken dashboard; (c) the artifact chat must verify the same before publishing (it already does — keep it that way).
9. **Publish flow (learned 2026-07-16/17 the hard way):** a REMOTE publish (cloud session via update_artifact) STRIPS the artifact's mcpTools allowlist — the ↻ Refresh button dies until tools are re-declared locally. And the artifact's own Chat can hold a STALE copy — it once rolled the live artifact back to v16c by "re-declaring tools" from its cache. Rules: (a) publish from LOCAL tasks or the artifact Chat, feeding it a file on disk and verifying DATA_VERSION before it ships; (b) if a cloud session must publish, expect to re-declare the seven mcpTools afterward via the artifact Chat, from a disk file, never from its memory; (c) the canonical on-disk source is **`mbm-review-receiver/dashboard_index.html`** — ONE file, version lives in its `DATA_VERSION` const, never in the filename. Update it in place, keep it committed, and point the artifact Chat at it for publishes ("verify DATA_VERSION = <expected> before publishing"). No more versioned loose copies. (d) **NEVER diagnose the live artifact's state from staged artifact content** (cloud `device_stage_files` with `artifact_ids`): the staging mount PINS a stale copy — on 2026-07-17 it served a 97,319-byte v16b file from the previous day while the stage RESULT reported the server's true fresh metadata (new mtime, new byte count). Only the stage-result **byte count / mtime** are server-truth; the file content can be days old. A false "the dashboard regressed!" alarm from this exact trap triggered a rule-(b)-violating remote publish that stripped the allowlist. To check what's live: compare the stage-result byte count against known build sizes, or ask the artifact Chat to report its own DATA_VERSION — never trust the mounted bytes. (e) **The same staleness applies to `device_bash`/local-task bash reads of a file another writer recently wrote:** on 2026-07-17 BOTH the cloud session's device_bash AND the artifact Chat's bash hashed an identical phantom mid-write snapshot (110,427 bytes, tail mid-CSS, no `</html>`) of a file that was in fact complete on the real filesystem — producing a false "truncated!" incident and a near-rollback. Rules: bash sha/tail verification is trustworthy only for writes YOU just made in the same flow (chunk-reassemble-then-sha is self-consistent — a stale view would fail the sha match). To verify a file some OTHER writer produced, stage it back to the cloud with `device_stage_files` (paths, not artifact_ids) and hash the staged copy — that path reads the real filesystem. Two independent bash views agreeing is NOT confirmation; they can share the same stale cache.

## 1. Metric definitions (label → exactly what it is)

### North Star

**Badge reconciliation (added 2026-07-17):** the North Star counts **status-active, non-comp, non-test paying patients**. Hint's plan badges count memberships in EVERY status (unpaid, pending, comps, test accounts), so badge totals routinely exceed the tile — that gap is rendered ON the tile from `SNAPSHOT.members.active_recon` (`unpaid_concierge`, `pending_concierge`, `comp_active`, `test_active_excluded`), copied each bake from the feed's `active_members.reconciliation_by_status` grid. **`unpaid_concierge` > 0 fires an amber billing alert** — that's a live patient not being billed. Manual member entry stamps a FULL timestamp (`new Date().toISOString()`), not date-only — date-only compared before same-day feed timestamps and silently made manual overrides impossible (bug found+fixed 2026-07-17, v17m).
**Profitability widget (added 2026-07-18, v18b — TOP of dashboard):** `SNAPSHOT.finance` = Hint revenue − QuickBooks costs, both true MTD. Revenue = payments **collected** in Hint this month (feed `revenue_mtd.collected`, exporter probes billing endpoints defensively and reports `endpoint`/`amount_field`/`cents_assumed` — **verify one payment against the total on first run, and read the cents-vs-dollars warning if present**). Costs = QuickBooks P&L **total expenses** MTD via the QuickBooks connector. Net + margin computed at render only when BOTH sides present; nulls render "—" with per-side fix instructions, never $0 (zero ≠ null). On-widget caveat: QB costs lag entry/categorization; partial month until EOM; "not GAAP, a pulse." No trend arrows. Sources `qb` + `kit` may have `as_of:null` (= never pulled): `srcState` returns `{cls:"stale", ageH:Infinity, unwired:true}` and chips show "never" — never NaN.

**Checklist signups (added 2026-07-18):** `SNAPSHOT.kit` = perimenopause checklist form signups MTD + list total, from Kit v4 API (form subscribers created this month). This is the Meta perimenopause campaign's REAL conversion (Meta deliberately never sees signups — health-data rule; see MBM_META_ADS_NOTES.md). Ad-driven share via GA4 `utm_campaign=perimenopause_checklist`.

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
| Booking + phone taps | `booking_click` + `phone_click` **eventCount — EVENTS, not people.** People average ~1.9 taps each (verified 2026-07-17: 64 taps = 34 totalUsers). Never label taps as "people". | GA4 |
| Unique tappers / reachers | `totalUsers` deduped across booking+phone (one no-dimension query with inList filter), and `totalUsers` on `click`→hint.com. The leak panel uses THESE for rates — tap-based rates overstate the early leak ~2×. Caveat: totalUsers = unique browsers/devices; cross-device double-counts. Refresh alongside event counts. | GA4 |
| Reached booking channel | `click` events with `linkDomain == mtbakermedical.hint.com` PLUS `phone_click` events — **phone-first bookers count**. People = deduped totalUsers via orGroup filter: (eventName=phone_click) OR (eventName=click AND linkDomain=mtbakermedical.hint.com); per-channel via the same filter + sessionDefaultChannelGroup dimension (per-channel rows NOT summable). GBP profile calls (Performance API) live outside GA4 and are never deduped into these counts. | GA4 |
| Channel economics table | Per channel: spend (window labeled, never silently blended), taps, people→booking channel, $/person. Refresh alongside funnel queries. Nextdoor unattributable until its ad URLs carry UTMs. | GA4 + Ads + manual |
| Consults booked | running tally (above) | Hint |
| New paid members | paid new members (above) | Hint |

Deliberate exclusions: **no impressions band** (reach, not funnel — it dwarfs real stages into invisibility); **`form_start` removed everywhere** (the GA4 event was never implemented on the site — it can only ever read 0). The old North-Star "New leads" (taps bucketed by channel) is gone: taps are taps, and they're labeled as taps.

Consults are NOT a strict subset of handoffs (phone/walk-in bookings exist); members can close from prior-month consults. The funnel says so on-face.

### Attribution
"New members by lead source" = the self-reported Lead Source field in Hint, aggregate counts. **This is the primary attribution truth at MBM's volume** — last-click GA4 systematically undercredits GBP/referral/word-of-mouth across a ~3-week, 5-visit consideration cycle ending off-domain. The quarterly rollup of this bar is the real channel report. GA4 channel splits are directional decoration by comparison. Gap as of 2026-07: 0 of 3 July members have Lead Source set in Hint — fix at signup intake.

**LSA attribution rules (added 2026-07-17):** "Google Local Services" must be added as an option to the **Hint Lead Source intake** list (the "how did you hear about us" field), so LSA-sourced members can self-report the channel. **LSA lead counts are channel telemetry, NOT member attribution** — a forwarded phone/message lead is a contact, not a signup; do not read LSA leads as members. LSA-attributed signups are tracked **Concierge vs Service-Only**, and the channel is judged on its **Service-Only economics**, with Concierge conversions counted as upside (not the base case). Until "Google Local Services" is a Hint lead-source option and members select it, the LSA tile's lead counts stand alone and are not reconciled into the member attribution bar.

### Paid
| Tile | Definition | Caveats |
|---|---|---|
| Google Ads spend/clicks/CPC | GAQL v21, campaign resource, **true MTD** | CPC ~$3–4, not the folkloric $2 |
| Ad-attributed taps | GA4-imported booking_click+phone_click from **`metrics.all_conversions`** | Immune to the primary/secondary goal misconfig that zeroed `metrics.conversions` Jul 2–16. Campaign-table "Conv*" column = primary conversions, annotated. |
| GBP local actions | Google-hosted conversion actions (calls, directions, website visits, other) from Ads | **Ad-driven only.** Engagement on Google surfaces, 24–48h lag, click-to-call undercounts real calls. **Never summed with site conversions** (double-counting). |
| GBP profile actions | Business Profile Performance API: CALL_CLICKS, WEBSITE_CLICKS, BUSINESS_DIRECTION_REQUESTS, daily | **All-source** (organic + maps + ads) — broader than local actions. Reached via the **Zapier GBP connector's raw GET** (its OAuth carries business.manage — no separate Google API approval needed). Location ID `3763111265482720361` (from the business.google.com URL `#mpd=~<id>`). Google lags 2–3 days; missing days in the response = 0. Zapier CENSORS ids in account-listing responses, but a URL you construct yourself works fine. Pre-category-change baseline: June = 13 calls / 29 site clicks / 67 directions; Jul 1–15 = 11 / 11 / 51 (Jul 14: anomalous 22 directions — investigate before crediting the category change). |
| Meta | Manual weekly Ads Manager pull. "Results" = **landing-page views, not leads** (no lead event on pixel) | Campaign flagged "Not delivering — Ad errors" since ~Jul 9; numbers frozen. |
| Nextdoor | Manual weekly pull; spend/impr/clicks only (no pixel) | — |
| **LSA (Google Local Services)** | Pay-per-lead. Leads MTD (phone + message), charged vs credited, and spend. **Source:** `local_services_lead` GAQL v21 searchStream (`SELECT local_services_lead.lead_type, local_services_lead.lead_status, local_services_lead.credit_details.credit_state, local_services_lead.creation_date_time FROM local_services_lead WHERE creation_date_time` MTD), customer 1167880168. Split `lead_type` PHONE_CALL vs MESSAGE; charged/credited from `lead_status` + `credit_details.credit_state`. | **LSA phone leads are a FOURTH distinct call signal (Google forwarding numbers) — NEVER summed with GBP CALL_CLICKS, Ads local actions, or GA4 `phone_click`.** No trend arrows (small-n). Campaign `LocalServicesCampaign:SystemGenerated:00064a0427c4cb6a` is **PAUSED** — **zeros are real zeros while paused** (verified pull path 2026-07-17: HTTP 200, valid fieldMask, zero rows), never hatched-stale. **LSA spend flows into "Google Ads spend MTD" automatically** — the campaign-spend GAQL is `FROM campaign` with no type filter and the LOCAL_SERVICES campaign appears in it (verified live, cost_micros 0 while paused), so it lands in cost-per-member / per-consult once live. Tile caveat "incl. LSA from &lt;date&gt;" and the GBP-note confound render only when the `LSA_GO_LIVE` constant is set (null until go-live). |
| **LSA test — kill/keep tracker** | **Decision-rule tracker, NOT a KPI trend — no arrows, no rates, no per-lead cost math (small-n).** `SNAPSHOT.lsa.test`, **cumulative since 2026-07-17** (deliberately NOT MTD — the test window crosses month boundaries). Fields: `leads_charged_cum` = billed leads since test start (`local_services_lead` GAQL, `creation_date_time >= 2026-07-17`, charged only — **disputed/credited excluded** via `lead_status` + `credit_details.credit_state`); `spend_cum` = LOCAL_SERVICES campaign cost, `segments.date >= 2026-07-17`; `patients_attributed` = `members_feed.json.lsa_test.patients_attributed` (Hint Lead Source → `google_lsa` bucket, **PAID patients only**; pending tracked separately as `attributed_pending` — nurture can still cancel them at day 30). Renders as progress toward the 12-lead checkpoint: "N/12 billed leads · $X · N attributed". | **Decision rule (locked 2026-07-17):** evaluate at **12 billed leads (~$600)**: ≥2 patients → **KEEP** · 1 → extend to **20 leads** · 0 → **PAUSE**. Override: **any Concierge conversion = automatic keep**. Working value: GLP SO patient ≈ **$500** (Charlie, 2026-07-17 — may revise). The rule text on the tile is **static config, not data**. "Attributed" depends on intake Lead Source discipline — **a zero can be missing attribution, not channel failure** (the tile says so). `patients_attributed: null` = the upgraded feed hasn't run yet → render "—", never 0 (zero ≠ null). A dead lsa pull keeps last-good values + stale hatch — **never renders a fresh 0/12**. Exporter side: `map_source` maps "local service"/"LSA" → `google_lsa` **before** the generic google catch-all (else LSA vanishes into "google" and attributed stays 0 forever); `SOURCE_KEYS` gained `google_lsa`; feed key `lsa_test` (since/patients_attributed/attributed_pending/attributed_unverified_payment/signups_checked). |

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
          + LSA (Google Ads GAQL v21 searchStream: local_services_lead, MTD by
            creation_date_time → SNAPSHOT.lsa {leads_phone/message/charged/credited,
            spend}; sources.lsa.as_of. Campaign PAUSED → real zeros; the campaign-spend
            GAQL already carries LSA spend, FROM campaign no type filter)
          + LSA TEST cumulative fields (same pull, second window: creation_date_time
            >= 2026-07-17 → SNAPSHOT.lsa.test.leads_charged_cum [charged only,
            disputed/credited excluded] ; campaign cost segments.date >= 2026-07-17
            → spend_cum ; members_feed.json.lsa_test.patients_attributed +
            attributed_pending → SNAPSHOT.lsa.test. NOT MTD — never reset at
            month rollover. Feed lacking lsa_test key → leave nulls, do not bake 0.)
          + FINANCE: revenue from feed revenue_mtd.collected (+ as_of = feed generated_at)
            → SNAPSHOT.finance.revenue. COSTS from QuickBooks — call the QuickBooks
            connector's company_info tool first (opens the OAuth session), THEN
            profit_loss_quickbooks_account with periodStart = current month's 1st
            (YYYY-MM-01) and periodEnd = today; take the top-level totalExpenses →
            SNAPSHOT.finance.costs.mtd + as_of(now ISO) + sources.qb.as_of(now ISO).
            QB income is ~$0 (concierge revenue is collected in Hint, not booked in QB),
            so take ONLY totalExpenses — ignore QB's own net/income. Net + margin are a
            render-time computation (Hint revenue − QB costs); never hardcode net.
            QB connector unauthorized → leave nulls + report, never fake.
          + KIT: v4 API GET form subscribers created this month (perimenopause
            checklist form) → SNAPSHOT.kit {checklist_signups_mtd, list_total} +
            sources.kit.as_of. Kit connector unauthorized → leave nulls + report.
          + active_recon (feed active_members.reconciliation_by_status →
            SNAPSHOT.members.active_recon {unpaid_concierge, pending_concierge,
            comp_active, test_active_excluded, as_of})
          + EXTEND the daily arrays through yesterday every bake — the anomaly
            detectors read them; a stale window rots silently (missed on the
            first task bake 2026-07-17: totals updated, dailies didn't)
          → updates SNAPSHOT block in index.html, sets sources.*.as_of per source
          actually refreshed, bumps DATA_VERSION, checks updatedAt, republishes
          + pushes the private hosted partner copy AS THE LAST STEP (after the
            artifact publish verifies): `python push_hosted_dashboard.py` re-uploads
            the `mbm-dashboard` Cloudflare Worker with the freshly-baked HTML inlined.
            Hosted URL (share-with-partner, aggregate-only, noindex):
            https://mbm-dashboard.charlie-956.workers.dev/dash-8a19bca7b8cbc5f3724077d0
            Worker acct 95619789467f5b8aa49e44428e1ed443; CLOUDFLARE_API_TOKEN has
            Workers Scripts:Edit but NOT KV — so HTML is inlined, not KV-backed. The
            script hides the ↻ live-refresh button when the Cowork bridge is absent.
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

## 4. Known gaps / status

**⚡ 2026-07-18 BOOKING CUTOVER HAPPENED (see mbm-book-ops skill): the site's booking
CTAs now go to mtbakermedical.com/book-beta (MBM's own booking system), NOT
mtbakermedical.hint.com.** The §4 cutover checklist is ACTIVE: (a) the funnel's
"tap→Hint arrival ≈100%" framing is obsolete — bookings complete ON-SITE now;
(b) GA4 outbound-linkDomain "handoffs" to hint.com will collapse to ~0 — that is
EXPECTED, not a tracking break (do not fire the zero-tap alarm logic at it);
(c) booking_click still fires on CTA click (verified in bundle) so taps remain valid;
(d) consult_count.py's Contact-attendee rule MUST be re-verified against bookings
created by the new system (it creates Hint patients instantly — check the attendee
shape); (e) Meta CAPI Schedule beacon fires pre-navigation and /book* suppression
still applies — verified live 2026-07-18. Funnel definitions in §1 need a revision
pass once a few days of /book-beta data exist.

 (updated 2026-07-16 late)
- **IN FLIGHT — first-party booking software** (separate task, 2026-07-17): replaces Hint's hosted booking flow. At cutover this contract changes: (a) funnel gains measurable booking_start → booking_complete stages and the "Hint arrival ≈ 100% / outbound telemetry" note retires; (b) `signup_complete` starts firing for real — re-promote the Ads "Sign-up" action to primary THEN, not before; (c) lead_source gets auto-collected at booking (buckets must match the dashboard's, + "Google Local Services"); (d) UTM/gclid stored on the Hint record = real channel→member attribution; (e) VERIFY consult_count.py's Contact-attendee rule against how the new system writes to Hint — if it creates patients at booking, the running tally breaks and must ship a matching update. PHI: booking data persists only in Hint/BAA'd systems; CF KV stores hashes at most.
- **Active-member counts**: `active_members` (whole-book, status=='active', F&F/test excluded) now in `export_dashboard_members.py`; dashboard prefers the feed-baked `SNAPSHOT.members.active` over manual entry once the refresh task bakes it. Verify `status_histogram` on first run — if Hint uses a status other than `active`, adjust the filter.
- **Terminations**: `terminations_mtd` field added (end-date-in-month basis, defensive). If `end_field` comes back null with `end_status_without_date > 0`, extend `_END_KEYS` in the exporter. Presentation rule: net growth = adds − terms; at ~150 members show TTM churn + cohorts, never monthly churn %.
- **Lead Source discipline**: ask "how did you hear about us" at signup, record in Hint. 0/3 July members have it. The only durable attribution.
- **Hint booking-flow visibility**: asked Hint support (in-app chat, 2026-07-16) whether the hosted signup allows a GA4/GTM tag or UTM passthrough; custom-signup API is the fallback. Handoff stage remains the proxy meanwhile. NOTE: Hint also has an open reply waiting on Charlie in the "New phone number setup" thread (they asked which area code).
- **GBP Performance API**: ✅ SOLVED 2026-07-16 via Zapier raw GET (no Google approval needed). Baked into the daily refresh contract (§2).
- **Meta**: diagnosed 2026-07-16 — the "Not delivering" campaign's ad is **Rejected** ("doesn't comply with our Advertising Policies", generic policy strike; likely personal-health policy given the creative). Untouched per Charlie. Options: edit creative + resubmit, request re-review via Business Support Home, or pause. Also pending in Ads Manager: "New Leads Ad" in draft, 3 unpublished changes ("Review and publish (3)"), and a **"Verification may be required soon"** banner — identity verification is Charlie-only, do not automate.
- **Google Ads goal config**: booking_click + phone_click restored to primary 2026-07-16; "Sign-up (signup_complete)" **demoted to secondary** 2026-07-16 (the GA4 event doesn't exist — re-promote only after the event is actually implemented). Watch: Google search-panel showed "Branded | Bellingham | Search — NOT ELIGIBLE, your ads aren't showing" on 2026-07-16 — check the campaign's status/budget/policy in Ads if it persists.
- **Ahrefs units**: exhausted 2026-07-16; SEO live-refresh effectively disabled until reset. Weekly browser read is the fallback.
- **GBP category on SERP**: knowledge panel still showed "Family practice physician" on 2026-07-16 evening — propagation lag, recheck in 24–48h.
