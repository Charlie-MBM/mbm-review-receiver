# Mt. Baker Medical — Review Request Automation

## What this repo does

A daily Python CLI script that polls Hint Health for new patient visits / invoices and triggers Spruce review-request SMSes — capped at 3 sends per patient with 30-day spacing.

**Runs locally on Charlie's Windows machine via Windows Task Scheduler.** Not hosted anywhere. No webhook receiver listening on a port. No Claude inference in the data path.

## Why it's not a hosted webhook receiver

The original design was a Flask app receiving Hint webhooks at a public URL. That requires BAA-eligible hosting (Render Scale $499/mo, Fly.io Compliance $99/mo, etc.). Charlie's preference was to keep PHI inside the covered entity — his own machine — rather than introduce another BAA hop. So this is a CLI poller instead.

The Flask routes in `hint_webhook_receiver.py` are preserved as a fallback / library of functions, but they're not exercised at runtime.

## Canonical runbook

Full documentation lives in the docs repo:

**`Charlie-MBM/mt-baker-medical-website/project/REVIEW_AUTOMATION.md`**

That runbook covers:
- Architecture rationale (cross-referenced to HIPAA_AUDIT.md)
- Repo file layout and what each module does
- Required environment variables
- Script lifecycle per invocation
- Windows Task Scheduler setup steps
- Operational ops (dry-run, manual trigger, log/state inspection, pause/resume)
- Failure modes and responses
- Pre-launch test checklist

If you're setting up the script on a fresh machine, read that file first.

## Quickstart (assumes Python 3.10+ on PATH)

```
git clone https://github.com/Charlie-MBM/mbm-review-receiver.git
cd mbm-review-receiver
py -m pip install -r requirements.txt
cp .env.example .env       # then fill in Hint + Spruce API keys
py send_review_requests.py --dry-run
```

If `--dry-run` looks right, drop the flag for real sends. Schedule via Task Scheduler per the runbook.

## Files in this repo

- `send_review_requests.py` — CLI entry point. Polls Hint, applies rate limits, sends via Spruce.
- `hint_webhook_receiver.py` — Library of helpers (PHI extraction, state management, Spruce send, rate-limit rules). Originally a Flask app; route handlers are dead code in the poller path but kept for reference.
- `requirements.txt` — Python deps.
- `.env.example` — Template for the local `.env`. Real `.env` is gitignored.
- `.gitignore` — excludes `.env`, `patient_state.json`, `send_log.txt`, `__pycache__/`.

Files that exist on Charlie's machine but are NOT in this repo:

- `.env` — API keys and secrets.
- `patient_state.json` — per-patient send history. Don't commit (PHI by HIPAA's broad definition).
- `send_log.txt` — daily run log.

## What this repo does NOT do

- Receive Hint webhooks (the poller architecture doesn't need them)
- Serve the `/feedback` POST endpoint from the website's /review page (that's a separate Cloudflare Worker concern; currently the form falls back to mailto)
- Reply to public reviews (human-only per HIPAA review-reply rules; see HIPAA_AUDIT.md)
- Drive the NAP audit (separate autonomous Claude scheduled task; see NAP_AUDIT.md)
- Send bulk one-time campaigns (do those via Spruce's Bulk Messages UI manually)

## License

Private / internal use only.
