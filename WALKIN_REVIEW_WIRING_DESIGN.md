# Walk-In Review Wiring — Design (W-REVIEW-DESIGN, 2026-07-01)

**Status: DESIGN ONLY. Build is GATED on Lee's consent review (TCPA / walk-in consent). Nothing here is implemented.**

Goal: wire $150 Walk-In visits into the review-request system. Walk-ins are non-members, so today they are excluded twice: (1) the member-only guard in `send_review_requests.py` skips them, and (2) Hint `/api/provider/appointments` exposes no appointment type, so a Walk-In Visit (`appty-e60503ee36b171e6`) is indistinguishable from a free consult at the trigger. Charlie's direction: trigger off the **paid $150 Walk-In charge/invoice**, not the appointment.

References: SKILL mbm-review-ops; `send_review_requests.py`; `hint_webhook_receiver.py`; MOAT_PLAN 2026-06-22 + 2026-06-25/29 entries; SAMEDAY_URGENT_CARE_BRIEF_2026-06.md (§6, §A).

---

## 1. Trigger source options in the Hint API

What the existing code actually proves about Hint provider-API capabilities:

| Endpoint | Evidence in repo | Date filter | Pagination |
|---|---|---|---|
| `GET /api/provider/appointments` | poller, daily summary, nurture | `start_date`/`end_date`, windows must be <=31 days | `limit`/`offset` (100) |
| `GET /api/provider/memberships` | daily summary, nurture | `created_at[gte]`, `status=` | `limit`/`offset` |
| `GET /api/provider/patients` / `/{id}` | everywhere | none used | `limit` + `x-total-count`; list pagination flaky (`_lookup_by_phone` note) |
| `GET /api/provider/patients/{id}/payment_methods` | nurture | n/a | n/a |
| `GET /api/provider/interactions` | old trigger, deprecated | NONE, capped at 10 rows | none |
| Webhook event `customer_invoice.paid` | `hint_webhook_receiver.py` handler (dead code) | n/a (push) | n/a |

Options for detecting a paid $150 Walk-In charge:

- **Option A (recommended): poll an invoices/charges list endpoint.** e.g. `GET /api/provider/invoices` or similar with a paid-date filter. **UNVERIFIED — no script in this repo has ever called an invoice/charge REST endpoint.** The `customer_invoice.paid` webhook proves the invoice object exists (has `id` + `patient_id`) but not that a pollable list endpoint exists, nor its filter/pagination behavior, nor whether line items (charge item name/id, amount) are exposed. Build day step 1 is a read-only probe script (pattern: `_probe_hint_*.py`).
- **Option B: per-patient invoice subresource.** e.g. `GET /api/provider/patients/{id}/invoices`. Also **UNVERIFIED**. Even if it exists it needs a patient list first, so it only works as a lookup after some other signal, not as the primary trigger.
- **Option C: webhook `customer_invoice.paid`.** Verified to exist, but rejected: the poller architecture deliberately replaced the hosted webhook receiver (no public endpoint, no BAA-eligible hosting). Keep as fallback knowledge only.
- **Option D: appointments endpoint.** Dead end for identification. `/appointments` does not expose appointment type (confirmed 2026-06-25/29; title/description mostly empty). Useful only as a secondary cross-check, not the trigger.

Matching rule once the endpoint is known: match on the **Walk-In charge item id/name**, not on amount == $150. Amount matching breaks the day the price changes or another $150 line item appears. If line items are not exposed, fall back to amount + a manual review log line, and flag it.

## 2. Proposed design: walk-in branch in the daily poller

One new branch in `send_review_requests.py`, same run, same state file, feature-flagged off.

- **Feature flag:** `WALKIN_REVIEW_ENABLED` in `.env`, default `false`. The branch is a no-op until Lee clears (see §4). Flag check is the first line of the branch.
- **Detection query:** fetch paid Walk-In charges/invoices with paid/created timestamp >= `last_run_at` (same `_poller_meta.last_run_at` cursor, same clean-run-only advance so failures retry). Exact params depend on the §1 probe.
- **Charge -> patient -> phone:** invoice `patient_id` -> `fetch_patient(pid)` -> `extract_phi_minimal(patient)` (already handles `chosen_first_name` preference and the `phones` list of `{number, type}`; keep `_normalize_phone_e164` in the send path). Skip non-`pat-` ids (phantom-id guard, 6/29 hardening).
- **Member-only guard:** `is_active_member()` stays untouched and continues to gate the appointment-triggered member flow. The walk-in branch bypasses it **explicitly and only inside the branch** — never by weakening the guard function or its call site in the member loop. In fact the walk-in branch inverts it: if the payer IS an active member, skip here and let the member flow own them (their visit shows up in `/appointments` anyway).
- **Consent gate (in-branch):** require `electronic_communication_consent_accepted == true` on the patient record (per SAMEDAY brief §A.2), plus whatever Lee adds. Belt and suspenders on top of the staged Walk-In consent doc.
- **Idempotency via patient_state.json (existing schema, no migration):** schema stays `{ "<patient_id>": {"count": int, "last_ask_ts": iso} }` + `_poller_meta`. Walk-in rule: ask only if the patient has **no prior ask at all** (`count == 0`). That single check gives (a) one-ask-ever for walk-ins, (b) idempotency across retry runs when `last_run_at` did not advance (the successful send already recorded `count=1`), and (c) the §3 dedupe for free. Recording still goes through `_record_request_sent()`. Optional additive field `"trigger": "walkin"` for debuggability; readers must tolerate its absence.
- **Cadence/copy for non-members:** walk-ins are one-off visitors. **Single ask, no follow-up sequence** — no 30-day re-asks, no cap logic beyond the `count == 0` gate. Click-tracker suppression applies unchanged (hashed fname check before send). Copy: keep practice voice, mission framing, DONE-only, no physician name; at most a light variant ("Thanks for coming in to see us") — any variant goes through the §0 brand gate first. Timing: the 10am next-morning run is the default; whether walk-ins need a same-day send is an open question (§6).
- **Dry-run:** `--dry-run` / `DRY_RUN=true` must apply to the branch exactly as it does to the member flow, and **must NOT record state**. The 2026-06-22 bug (dry-run wrote `count`/`last_ask_ts` and "burned" patients' spacing without any SMS going out) is fixed via the DRY_RUN check inside `_dispatch_to_bridge()`; the walk-in branch must dispatch through `_dispatch_to_bridge()` (or replicate that exact guard) and count sends off its bool return, never off state diffs.
- **Watchdog:** no change needed. The branch runs inside the same invocation, so `check_poller_health.py` (26h staleness on `_poller_meta.last_run_at`) covers it automatically.
- **PHI:** unchanged perimeter. Hint -> Charlie's PC -> Spruce. Nothing new touches Cloudflare beyond the existing hashed click-tracker; nothing routes through Cowork/Claude.

## 3. Interaction with the existing member flow (dedupe)

Scenario: patient walks in (walk-in ask fires), then joins as a member (member flow now sees their appointments).

- **Rule: one shared state record per patient_id is the dedupe.** Both branches read/write the same `patient_state.json` entry.
- Walk-in ask happened -> `count >= 1`, `last_ask_ts` set -> the member flow's existing 30-day spacing check (`_should_send_review_request`) prevents any near-term second ask. No same-visit double ask is possible.
- After 30+ days as an active member with a new visit, a member-flow ask is allowed and correct — that is a new care event, not a double ask.
- Walk-in branch's `count == 0` gate also covers the reverse order: an existing/former member with any prior ask never gets a walk-in ask.
- If they clicked the Google CTA after the walk-in ask, click-tracker suppression is forever, both flows.
- **Nurture collision (flag):** the sibling nurture poller texts non-members with pending memberships. A walk-in who is later assigned a pending membership could get a nurture text and a review ask in the same window. The two systems share no state today. See open questions.

## 4. Compliance

- **HARD GATE: the branch does not go live until Lee clears the walk-in batch.** TCPA/SMS consent basis for non-members is her open question #4 (project/Walk_In_Visit_Questions_for_Lee.md): review asks are telemarketing, not the TCPA healthcare exemption; members rely on the ePHI waiver + established patient relationship; a one-off walk-in has neither by default. Proposed basis = the staged "Walk-In Visit Agreement & Consent to Treat" (drafted, in Hint UNPUBLISHED) + `electronic_communication_consent_accepted`, but Lee decides whether that is sufficient or booking-time language is needed. `WALKIN_REVIEW_ENABLED=false` is the enforcement mechanism until then.
- **WA MHMD:** same posture as the existing system. Practice-voice SMS, minimum-necessary payload (first name + phone only leave Hint), SHA-256-hashed click-tracker keys on Cloudflare KV (no BAA there), Spruce and Hint under BAA.
- **Member-only guard history (do not silently regress it):** added 2026-06-10 after non-members were observed receiving review texts; it stood in for exactly the consent basis Lee is now ruling on. The walk-in branch is the deliberate, consent-gated exception; the guard itself stays for every other path.
- **Google policy:** a walk-in is a genuine patient with a paid visit — eligible reviewer, unlike the free-consult prospects the guard was built to exclude.

## 5. Test plan

- **No real patient data, ever.** All probing and testing uses synthetic/dummy records; Charlie's own Hint record `pat-z7Pu6cu2FtQg` is the only authorized real record for testing.
- **Step 1 — read-only endpoint probe** (build day): `_probe_hint_invoices.py`, GET-only, against Charlie's record where a patient id is needed. Answers §6 Q1-Q3. No writes.
- **Step 2 — synthetic paid walk-in charge on Charlie's record.** Create the $150 Walk-In charge item on `pat-z7Pu6cu2FtQg` in the Hint UI and mark it paid. How to do that without real money movement (offline/comp payment? void after?) is an open question for the Hint UI, not the API.
- **Step 3 — dry run:** `py send_review_requests.py --dry-run` with `WALKIN_REVIEW_ENABLED=true`. Confirm: the charge is detected, patient mapped, phone extracted, "[DRY_RUN] Would SMS" logged, and `patient_state.json` byte-identical before/after (the 6/22 regression check).
- **Step 4 — single live test to Charlie's number:** `Remove-Item patient_state.json` first (he is click-suppressed forever and spacing-blocked from prior tests), set `DRY_RUN=false`, run once, verify one SMS to (360) xxx-8094 from (360) 295-9241, verify state now shows `count=1` for his id, then restore/clean state.
- **Step 5 — negative tests (dry-run):** active member with a walk-in charge -> skipped by branch; patient with `count>=1` -> skipped; consent flag false -> skipped; flag off -> branch no-op.
- **Step 6 — re-run idempotency:** run twice without advancing the cursor; second run must send nothing.

## 6. Open questions + build size

1. **Does the Hint provider API expose a pollable invoices/charges list endpoint, and with what date filter + pagination?** UNVERIFIED; nothing in the repo has ever called one. (Probe.)
2. **Does the invoice/charge payload expose line items** (charge item id/name, amount, paid status, `patient_id`)? Needed for the item-id match; amount-only match is the weak fallback. (Probe.)
3. **What is the stable identifier of the Walk-In charge item** to match on? (Probe + Hint UI.)
4. **Lee: TCPA/SMS consent basis for walk-in non-members** — is the walk-in consent doc + `electronic_communication_consent_accepted` sufficient? THE gate. (Already in her batch.)
5. **How to create a paid $150 test charge on Charlie's record without real money movement.** (Hint UI investigation.)
6. **Timing:** is the next-morning 10am run acceptable for one-off patients, or add a same-day afternoon poll for walk-ins? (Charlie; brief §7 argued same-day converts better.)
7. **Nurture collision:** should the walk-in branch suppress the ask when the patient has a pending membership in the nurture state (or vice versa)? (Charlie ruling; small cross-check either way.)
8. **New-patient entry flow** (booking link requires sign-in; disabled checkout page) is unresolved and could change where/when the charge gets created relative to the visit. (Operational, tracked in MOAT_PLAN.)
9. **Copy variant for walk-ins** — reuse member SMS verbatim or a walk-in-flavored line? Any variant passes the §0 brand gate. (Charlie.)

**Estimated build size:** one day or less once Lee clears and Q1-Q3 resolve. Probe script ~1-2h; walk-in branch in `send_review_requests.py` ~100-150 lines reusing `fetch_patient` / `extract_phi_minimal` / `_dispatch_to_bridge`; tests per §5 ~2-3h including the live single-send. Matches the half-day estimate in SAMEDAY brief §A plus probe + test overhead.
