# MBM Poller & Scheduled-Task Runbook

Single reference for every automated task running on Charlie's PC. **Read this before tinkering with any poller** so we stop rediscovering how they work. Two repos are involved:

- `C:\Users\charl\GitHub\mbm-hint-enrollment\webhook\` — consult intake, no-show, prospect alert
- `C:\Users\charl\GitHub\mbm-review-receiver\` — nurture, review, archive digest, dashboards

---

## GOLDEN RULES / gotchas (these bit us — don't relearn them)

1. **`.env` beats the `.bat`.** The scripts call `load_dotenv(..., override=True)`, so a value in `webhook/.env` or `mbm-review-receiver/.env` **overrides** any `set VAR=...` in the runner `.bat`. Example: the consult `.bat` says `SEND_ENABLED=false`, but `.env` has `SEND_ENABLED=true`, so it *does* send. Always check `.env`, not just the bat.
2. **`--dry-run` does NOT persist state.** Dry runs log what they *would* do but never write the state JSON. So a dry run can't corrupt or advance anything — but it also can't be used to "seed" state.
3. **Non-dry run with a gate OFF still writes state.** This is how the `{"id":"dry-run"}` sentinel bug happened (see history) — a create ran with `CREATE_ENABLED=false` (not `--dry-run`), stamped a fake id, and the idempotent guard then skipped that record forever. Gates now return `None` when off; guards only trust real `pat-…`/`mem-…` ids.
4. **Re-registering an S4U task needs an ELEVATED PowerShell.** Plain PowerShell gives `Register-ScheduledTask : Access is denied` when overwriting an existing task. Open PowerShell **as Administrator**.
5. **`schtasks /change /st` prompts for the run-as password on S4U tasks.** Don't type a password — instead re-register with `Register-ScheduledTask -Force` (S4U needs no stored password). Run the cmdlets **directly** in PowerShell, not wrapped in `powershell -Command "..."` (double-expansion mangles `$env:` vars).
6. **`Add-Content` to `.env` can glue onto the last line** if the file lacks a trailing newline. Always verify with `Select-String KEY .env` afterward — you want each `KEY=value` on its own line.
7. **Cowork's file view lags the laptop.** When Claude edits a file here, the sandbox's copy can be stale/truncated, so Claude's own `py_compile` may falsely fail. Source of truth = the laptop. Verify with `py -m py_compile <file>` on the PC.
8. **Hint API limits we confirmed:** cannot archive/deactivate a patient (no field, endpoints 404 — 200s on unknown fields are ignored); cannot create plans (list returns only id/name/plan_type); does not expose appointment *type* on the appointment object (title is null). Plan/appointment-type config is UI-only. **Appointments are READ-ONLY** (list only — no create/update/delete, no appointment webhooks; confirmed from developers.hint.com 2026-07-17). **Patient-create minimum payload = first+last only** — DOB and email both optional, verified in production 2026-07-17 (`pat-loR52tny36Oz`, name+phone only).
9. **Hint attendee reminders/notifications must stay OFF.** All 5 Free Consultation types deliberately have Hint's SMS/email reminders and attendee notifications disabled — the pollers own ALL patient texting via Spruce. Turning Hint's on = double-texting. (Practice-side email notifications stay ON.) Consult-type settings live at `one.hint.com/new_settings/appointment_types`; as of 2026-07-17: min notice **3h** (was 24h), "focus on" field **optional**, location hours Mon–Fri 9–6 / Sat 9–5 (`one.hint.com/new_settings/locations/bloc-fb8f99aaaf1263c8`).
10. **Hint ↔ Google Calendar two-way sync is live** (support.hint.com art. 10009535): Hint appts push to Google with type-name+link; Google events come back as title-less **busy blocks**. James's Google Calendar is therefore the cross-system conflict arbiter — the planned custom booking app writes there.

---

## Scheduled tasks (all S4U — run whether logged in or not)

| Task name | When | Runner | Script | What it does |
|---|---|---|---|---|
| **MBM Consult Intake Poller** | 5:30 PM daily | `webhook\run_consult_intake.bat` | `send_consult_intake.py` | FULL pass: reminders/confirmations; **auto-creates a Hint patient** for new online-booking prospects; **detects no-shows + sends Touch 1** (no-show work is EXCLUSIVE to this pass) |
| **MBM Consult Intake Intraday** *(added 2026-07-17)* | Hourly 8 AM–5 PM daily | `webhook\run_consult_intake_intraday.bat` | `send_consult_intake.py --skip-noshow` | Same-day-booking coverage (Hint min-notice dropped 24h→3h): confirmations/reminders/patient-create only; `--skip-noshow` so an attendee whose membership isn't entered yet is never texted "didn't connect" |
| **MBM No-Show AM Followup** | 9:15 AM daily | `webhook\run_noshow_am.bat` | `send_consult_intake.py --noshow-am` | No-show **Touch 2 (day 3) / Touch 3 (day 10)** + **day-15 archive queue** |
| **MBM-Nurture-Poller** | 9:30 AM daily | `run_nurture_poller.bat` | `send_nurture_sequence.py` | Post-consult non-converter nurture (pending membership + no card). Plan-branched copy. Day-30 cancel + archive-queue |
| **MBM-Review-Poller** | 10:00 AM daily | `run_review_poller.bat` | `send_review_requests.py` | Google-review request SMS after a real member visit |
| **MBM-Prospect-Form-Alert** *(3× daily since 2026-07-17)* | 9:00 AM / 12:30 PM / 3:30 PM | `webhook\run_prospect_alert.bat` | `send_prospect_form_alert.py` | Texts James+Charlie today's consult prospects missing HIPAA intake. **Delta alerts**: state tracks per-day `alerted_appt_ids`, so the 12:30/3:30 runs text only NEW same-day bookings — no repeats |
| **MBM Archive Digest** | Mon 9:00 AM | `run_archive_digest.bat` | `send_archive_digest.py` | Emails James+Charlie the patient IDs ready to archive by hand (Hint API can't archive) |

(Dashboards `mbm-dashboard-daily-fast` 9:30 AM and `mbm-dashboard-weekly-refresh` Mon 9:15 AM are Cowork scheduled tasks, not local pollers.)

---

## Per-task gates & state

**Consult Intake / No-Show** (`send_consult_intake.py`, `.env` = `webhook/.env`, or `../mbm-review-receiver/.env` as base):
- Gates: `CREATE_ENABLED` (patient auto-create), `SEND_ENABLED` (all Spruce sends), `NOSHOW_ENABLED` (no-show texts only), `HINT_ENV=production`. `--dry-run` forces all three off.
- No-show cadence: T1 evening (day 0), T2 day 3, T3 day 10, day-15 archive. Rebook link = `REBOOK_URL` (`https://mtbakermedical.hint.com/booking` — the generic picker; Hint doesn't expose consult type so no per-type routing).
- State: `webhook\consult_intake_state.json` (per-appt: `confirmed_at`, `reminded_at`, `patient_created`, and `noshow` block). Log: `webhook\consult_intake.log`. Archive queue: `webhook\noshow_archive_queue.csv`.

**Nurture** (`send_nurture_sequence.py` / `nurture_engine.py`, `.env` = `mbm-review-receiver/.env`):
- Trigger: Hint membership `status=pending` + no payment method on file. Suppress the instant a payment method appears or they get an active membership. `SEQUENCE_DAYS` = `[0,7,21]` agnostic; Concierge individual/couple get `[0,3,7,14,21]`.
- Go-live gate: `go_live_at` in `nurture_state.json._meta` (stamp once with `--go-live`). Until stamped, every prospect is treated as pre-existing → `needs_approval` (add mem_id to `nurture_approved.json`).
- Exclusions: `NURTURE_EXCLUDE_PLANS` (e.g. Friends & Family `pln-BhgiC3jP0yzq`) — never nurtured or auto-cancelled. `MEMBERSHIP_DENYLIST`, `DENYLIST_NAME_SUBSTRINGS` (ZZTEST etc).
- State: `nurture_state.json`. Archive queue: `nurture_archive_queue.csv`. Log: `nurture_engine.log`.

**Archive Digest** (`send_archive_digest.py`): reads both archive-queue CSVs, emails IDs + Hint links (no names) via Gmail SMTP (`GMAIL_IMAP_USER`/`_PASSWORD`, recipients `ARCHIVE_DIGEST_TO`). Dedup state: `archive_digest_state.json`. Silent on an empty week.

---

## Common operations

```powershell
# Did a task run + succeed?  (Last Result 0 = OK)
schtasks /query /tn "MBM Consult Intake Poller" /fo LIST /v | findstr /C:"Last Run Time" /C:"Last Result" /C:"Next Run Time"

# Read a poller's log
Get-Content C:\Users\charl\GitHub\mbm-hint-enrollment\webhook\consult_intake.log -Tail 30

# Dry-run (sends/writes nothing)
cd C:\Users\charl\GitHub\mbm-hint-enrollment\webhook ; py send_consult_intake.py --dry-run --debug

# Run a poller live now (idempotent)
cd C:\Users\charl\GitHub\mbm-hint-enrollment\webhook ; .\run_consult_intake.bat

# Change a task's time or (re)create it — MUST be an ELEVATED PowerShell:
$a = New-ScheduledTaskAction -Execute 'C:\Users\charl\GitHub\mbm-hint-enrollment\webhook\run_consult_intake.bat'
$t = New-ScheduledTaskTrigger -Daily -At '17:30'
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName 'MBM Consult Intake Poller' -Action $a -Trigger $t -Settings $s -Principal $p -Force

# Disable / enable / delete a task
schtasks /change /tn "MBM Consult Intake Poller" /disable
schtasks /change /tn "MBM Consult Intake Poller" /enable
schtasks /delete /tn "MBM Consult Intake Poller" /f

# Flip a gate live (verify after — watch for line-gluing)
Add-Content -Path "C:\Users\charl\GitHub\mbm-hint-enrollment\webhook\.env" -Value "NOSHOW_ENABLED=true"
Select-String NOSHOW_ENABLED C:\Users\charl\GitHub\mbm-hint-enrollment\webhook\.env
```

---

## PHI boundary (Charlie's standing rule)
Never route real patient data through Cowork or the Chrome extension (no BAA). Poller work happens **on the laptop** (covered entity) — Claude writes/edits scripts and reads config, but does not open patient screens through Cowork. Diagnostics use PHI-safe probes (opaque IDs, masked output) that Charlie runs locally and pastes back.
