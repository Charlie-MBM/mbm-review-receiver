# MBM Meta Ads — Operating Notes (2026-07-18)
Companion to the `mbm-meta-ops` skill. This file is the durable memory of the
2026-07-17/18 Meta work. The dashboard contract is DASHBOARD_METRICS.md; the
booking system is mbm-book-ops; this file covers Meta acquisition.

## IDs (fills the skill's TBDs)
- Ad account: **Mt. Baker Medical Ads `2472047889906342`** (business 1545468387180756)
- Pixel/dataset: `1609739400325815` (MBM pixel)
- CAPI relay Worker: `mbm-booking-capi` (Cloudflare, account charlie@mtbakermedical…)
  URL https://mbm-booking-capi.charlie-956.workers.dev — repo `meta-capi-booking-relay`

## State as of 2026-07-18 ~13:00 PT
- **CAPI is LIVE and verified**: META_PIXEL_ID + META_CAPI_TOKEN set on the Worker
  2026-07-18; test POST returned `{"ok":true,"metaStatus":200,"metaBody":"…events_received\":1…"}`.
  Site → Worker → Meta all confirmed. The pixel's "inactive / no events in 7 days"
  warning in Ads Manager is EXPECTED — there is deliberately NO browser pixel
  (server-side CAPI only, `Schedule` event on booking-CTA clicks, suppressed on /book*).
  Do NOT "fix" that warning by adding a browser pixel.
- Old "Promoting website" ad: REJECTED, campaign Not delivering — leave dead; superseded.
- Old "New Leads Ad" lead-form draft stack: **DISCARDED 2026-07-18** (deliberate).
- Blocked custom conversion "Lead — Perimenopause Checklist" (872601622552168):
  **DELETED 2026-07-18** (health-terms block; never received an event).
- NEW campaign in progress: **"MBM Perimenopause Checklist - Traffic"**
  (campaign 52637889730524 / ad set 52637889730924 / ad 52637889730724, all DRAFT).
  Objective Traffic, manual setup, conversion location Website, perf goal =
  maximize landing page views. Ad set "Perimenopause Checklist - Bellingham".
  Charlie hand-set: daily budget $10, location Bellingham WA +25mi (the Locations
  editor rejects synthetic clicks — see UI notes). Ad level: creative + copy per below.

## Decisions (do not relitigate without new facts)
1. **No Meta lead forms for health topics, ever.** Meta Lead Ad Terms §A prohibits
   collecting sensitive (health) data; a perimenopause lead form violates the terms
   it requires accepting. Also WA MHMD. Emails are captured first-party by Kit on
   mtbakermedical.com/perimenopause-checklist.
2. **Kill rule (Charlie-approved):** if the perimenopause traffic ad is rejected
   once, do NOT appeal — switch spend to the concierge campaign; perimenopause
   stays organic/Kit/Google-search.
3. **No AI-synthesized likeness of Dr. Scribner.** Real 30-sec iPhone footage beats
   generated video for a medical brand; Meta labels synthetic people.
4. Audience targeting: **geo/age only** — no health-interest targeting (policy trap).

## Approved copy (gate-checked: brand + Meta health rules)
**Campaign 1 — Perimenopause (traffic):**
- URL: https://mtbakermedical.com/perimenopause-checklist?utm_source=facebook&utm_medium=paid&utm_campaign=perimenopause_checklist
- Primary: "Perimenopause can be a long, confusing stretch — and clear information is
  surprisingly hard to find. Mt. Baker Medical put together a free, practical checklist:
  what to track, which labs to discuss, and the questions worth asking at your next
  appointment. No cost, no appointment needed."
- Headline: "The Perimenopause Checklist" · Desc: "Free guide from Mt. Baker Medical,
  Bellingham" · CTA: Learn More
**Campaign 2 — Concierge (traffic, stage after 1 is live):**
- URL: https://mtbakermedical.com/?utm_source=facebook&utm_medium=paid&utm_campaign=concierge_membership
- Primary: "Same-week appointments that start on time. Direct access to a physician who
  knows you. Mt. Baker Medical is a membership-based concierge practice in Bellingham —
  a small patient panel by design, cash-pay, no insurance billed."
- Headline: "Concierge primary care in Bellingham" · Desc: "Mt. Baker Medical ·
  Membership details online" · CTA: Learn More

## Creatives
Regenerate with `ad_creatives_build.py <assets_dir>` (assets = site repo
dist/client/assets/heroes). Concepts: A=peri photo band, B=doctor peri,
C=concierge photo band, D=doctor concierge; squares/stories + motion MP4s
(96f/24fps push-in + staggered text fade). Palette forest/cream/gold; Lora+Poppins.

## Ads-Manager automation notes (hard-won, 2026-07-18)
- The a11y tree HIDES Meta's dialogs/menus (find/read_page miss them). Working combo:
  JS `getBoundingClientRect` for coordinates → extension coordinate `left_click`
  (ref-clicks often no-op). Probe dialogs with
  `document.querySelectorAll('div[role="dialog"]')` — they ARE in the DOM.
- Text fields: click coords → ctrl+a → type (React inputs reject JS value-sets).
- The **Locations editor never opened for synthetic clicks** (both audience UIs,
  anchor .click() included) — that step needs a human.
- "Discard drafts" and "Review and publish" bundle ALL drafts in the account —
  always discard abandoned stacks before publishing a new one.
- Business verification banner pending ("may be required soon") — Charlie to do.

## Standing checks
- Weekly task W3: once Meta delivers, report Schedule (booking-intent) counts,
  labeled "server-side CAPI, not completed bookings".
- 2026-07-18 booking cutover (mbm-book-ops): CTAs now go to /book-beta — the CAPI
  beacon fires on CTA click BEFORE navigation, still valid; /book* suppression
  unchanged. Verify Schedule events continue post-cutover.
