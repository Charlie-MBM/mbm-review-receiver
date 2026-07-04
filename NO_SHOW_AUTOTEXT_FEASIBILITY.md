# No-Show Reschedule Auto-Text — Feasibility Verdict (2026-07-04)

**VERDICT: NOT FEASIBLE as designed. Do not build the poller-based no-show branch. There is no pollable "no-show" signal in Hint's provider API.**

Picks up the item the prior (Fable) moat session was mid-probe on when it ran out of credits: "use a subagent to check this stuff out." Checked directly against the Hint API spec + this repo's existing code; no live patient data was pulled (the authoritative answer came from documentation + committed code, so a live probe against real appointments was unnecessary and would have crossed the PHI/Cowork line).

References: SKILL mbm-review-ops; `send_review_requests.py`; `export_dashboard_members.py`; `hint_webhook_receiver.py`; WALKIN_REVIEW_WIRING_DESIGN.md; Hint OpenAPI `List All Appointments` (fetched 2026-07-04).

---

## 1. The design that was proposed

The Fable-session sketch: (1) staff marks the appointment "no-show" in Hint; (2) a new branch in the daily poller filters `/appointments` for no-show status; (3) fires a warm "sorry we missed you, rebook" text via Spruce, reusing the review-engine plumbing. Step 1 was assumed to produce a machine-readable status the poller could key off.

That assumption is false.

## 2. What Hint's API actually exposes (authoritative)

`GET /api/provider/appointments` returns an appointment object whose `status` field is an enum with exactly four values:

```
UNCONFIRMED, CONFIRMED, DECLINED, CANCELLED
```

Source: Hint OpenAPI spec, `Public.AppointmentBlueprint_all`, https://developers.hint.com/reference/appointmentlistallappointments.md (fetched 2026-07-04). The docs further note **"Declined appointments are not returned by this endpoint,"** so the effective returned set is `{UNCONFIRMED, CONFIRMED, CANCELLED}`.

There is **no `NO_SHOW` status.** Consequences:

- **A patient who no-shows stays `CONFIRMED`** (the appointment was confirmed; there is no attendance state-transition in the public API). So a no-show is indistinguishable at the status level from a patient who actually came in.
- **If staff instead mark it `CANCELLED`, it is indistinguishable from a courtesy cancellation** (patient called ahead). Texting every cancellation a rebooking nudge is a different, more aggressive product and would annoy patients who cancelled politely. Not the same feature.
- **The appointment payload carries no `cancellation_reason` / `reason` field.** Full property set: `id, start, end, status, title, description, created_at, updated_at, provider, location, attendees`. So even if a "No Show" cancellation reason exists in the Hint UI, it is not on the `/appointments` payload, and you cannot filter CANCELLED-because-no-show from CANCELLED-for-any-reason via this endpoint.
- **No appointment-level webhook exists** either. `hint_webhook_receiver.py` (lines 15-16) already documents "Hint does NOT have appointment-specific webhook events (no appointment.completed or visit.completed - Hint is a membership platform)." So the Fable "plan B: trigger off a Hint webhook" is also dead.
- The `no_show`/`no-show`/`noshow` strings in `export_dashboard_members.py` (line 381) were **defensive guesses**, not observed values. That code's own comment says it is written to "avoid guessing Hint's exact status string." Hint never emits them.

Net: staff "marking a no-show in Hint" does not yield any API-visible no-show signal. The foundational human step of the design produces nothing the poller can read.

## 3. Secondary finding (real, low-frequency, worth a fix): the review engine can ask a no-show for a review

Because Hint has no no-show status, `send_review_requests.py` (`fetch_member_visit_patients_since`, line ~198) treats **`CONFIRMED` + start time in the past = visit happened**, and only excludes `cancelled/canceled/declined`. A member who no-shows a confirmed appointment therefore remains eligible and could receive a "thanks for your visit, please leave us a Google review" SMS despite never coming in.

- **Likelihood: low.** Concierge panel, low appointment volume, members only. But it is real and awkward if it fires.
- **Root cause is identical to the no-show problem:** appointment status does not confirm attendance.
- **Cheap partial mitigation** without solving attendance: none that is reliable via status alone. The robust fix is the same primitive as §4 option 2 (anchor "visit happened" to a charge or clinical interaction, not to appointment status). Flagging for the record; not fixing here.

## 4. If Charlie still wants no-show rebooking texts — realistic options, ranked

1. **Staff-fired manual send (RECOMMENDED). Lowest effort, highest reliability, no Lee gate.** The front desk already knows it is a no-show (they are the ones who would mark it). Give them a Spruce saved-response / template they fire in two clicks from the patient's Spruce thread, or a tagged internal action. Reliability is 100% because a human who has the ground truth pulls the trigger, versus any inferred signal. Compliance: a reschedule text to an established patient who booked is **transactional** (care coordination / appointment), not telemarketing, so unlike the walk-in review-ask it does **not** need Lee's TCPA ruling. Engineering cost ~zero.

2. **Charge/interaction-absence inference (FRAGILE, not recommended now).** Poll `CONFIRMED` appointments whose start is in the past, then check whether a clinical interaction or a charge exists for that patient around that time; absence approximates a no-show. Problems: lagging and false-positive-prone (notes and charges are often written late, so "no artifact yet" != "did not show"), it inverts the review trigger, and it depends on the same unverified Hint charge/interaction matching the walk-in build needs. Only worth revisiting as a byproduct **if** the walk-in charge-polling gets built anyway.

3. **Wait for a Hint capability change** (a real no-show status or an appointment webhook). Not visible on their public API today; do not block anything on it.

## 5. The unifying strategic point

The review engine, the walk-in wiring (WALKIN_REVIEW_WIRING_DESIGN.md), and any no-show detection all want the **same missing primitive: a reliable "did this visit actually occur" signal.** Appointment status cannot provide it. A single charge-or-clinical-interaction lookup would (a) make member review-asks accurate (fixes §3), (b) enable the walk-in charge trigger, and (c) enable no-show inference (§4 option 2). That primitive is the thing worth scoping if/when the walk-in build proceeds. Building a bespoke no-show branch in isolation is not.

## 6. Recommendation

- **Do not build the automated no-show branch.** The trigger it needs does not exist.
- If no-show rebooking texts are wanted, do §4 option 1 (staff-fired Spruce template) - it is transactional, needs no code and no Lee gate, and is more reliable than anything automated we could build.
- Log §3 (review-ask-to-a-no-show risk) as a known low-frequency issue; the fix rides on the visit-artifact primitive in §5, not on a status filter.
- Revisit §4 option 2 only as a rider on the walk-in charge-polling work, never standalone.
