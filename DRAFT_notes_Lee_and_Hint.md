# DRAFT notes — Lee and Hint (DRAFTS ONLY, do not send)

Created 2026-07-16. Neither note has been sent, emailed, or posted anywhere. No real patient data appears below.

---

## NOTE 1 — to Lee (compliance/legal advisor) — Charlie's voice

Hi Lee,

Could you clear the consent basis for us to send Google-review-request texts to our walk-in and IV patients? That's the one thing gating this whole feature, and I want us on solid ground before it goes live.

Here's the flow. When a walk-in or IV patient comes in, we assign them a $0 "SO - Walk-In" or "SO - IV" membership at the visit. That assignment triggers the plan-attached consent (the Walk-In Visit Agreement, or the IV Therapy consent) and sets the patient's electronic_communication_consent_accepted flag on their record.

My question: is that signed consent doc plus the electronic-comms consent flag a sufficient TCPA basis to text these folks a review request? I want to flag that a review ask reads as telemarketing, not the TCPA healthcare-treatment exemption. Our members currently lean on the ePHI waiver plus an established treatment relationship, and a one-off walk-in has neither by default, so I'd rather ask than assume.

On cadence, we're proposing 3 touches: an initial text the next morning, a reminder at +3 days, and a final at +7 days. We stop immediately the moment they engage or reply STOP. Can you confirm that cadence and touch count are okay for this group?

To be clear, the whole mechanism is already built but it's gated OFF and stays that way until you sign off. Nothing sends until we hear from you.

Thanks, Charlie

---

## NOTE 2 — to Hint support — neutral, factual

Hi Hint support,

Quick provider-API question. Is there any way to list a patient's paid one-off charges or invoices via the provider API? We've tried GET /api/provider/invoices and it returns empty on every filter we pass, /charges and /transactions both 404, and /payments exposes no patient link or line items. We also don't see any charges embedded on the patient object. Alternatively, is the membership.created event the intended signal for one-off (non-membership) services? We just want to confirm the authoritative approach before building against it. Thanks!
