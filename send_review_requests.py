#!/usr/bin/env python3
"""
send_review_requests.py — Daily CLI poller for Mt. Baker Medical review automation.

Runs once per invocation:
  1. Loads last_run_at from local state (patient_state.json under _poller_meta).
  2. Polls Hint API for paid invoices and new memberships since last_run_at.
  3. For each unique patient: extracts PHI-minimal triple, applies 30d/3-cap
     rate limits via _should_send_review_request(), dispatches via Spruce SMS
     (and optionally email).
  4. Updates last_run_at to NOW (only on clean run), writes summary log line,
     exits.

See REVIEW_AUTOMATION.md in the docs repo for full architecture rationale.

Usage:
  py send_review_requests.py                   # normal daily run (uses env DRY_RUN)
  py send_review_requests.py --dry-run         # log-only, no sends
  py send_review_requests.py --since 2026-05-20T00:00:00+00:00
  py send_review_requests.py --allow-patient pt_xxx  # test mode, single patient
  py send_review_requests.py --lookback-days 7       # first-run window
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Auto-load .env from the script's directory BEFORE importing the receiver,
# because hint_webhook_receiver reads its config (API keys, DRY_RUN, etc.)
# from os.environ at import time. If .env isn't loaded yet, the receiver
# would see empty strings.
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    # dotenv is optional; system env vars still work without it.
    pass

# Import shared logic from the existing receiver. The Flask app instantiates
# on import but is never started, which is harmless.
import hint_webhook_receiver as receiver
from hint_webhook_receiver import (
    HINT_BASE_URL,
    HINT_API_KEY,
    _dispatch_to_bridge,
    _read_patient_state,
    _write_patient_state,
    extract_phi_minimal,
    fetch_patient,
    log,
    MIN_DAYS_BETWEEN_REQUESTS,
)

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

POLLER_STATE_KEY = "_poller_meta"
DEFAULT_LOOKBACK_DAYS = 1

# ── Walk-In / IV review branch config (feature-flagged; default OFF) ──────────
# Separate, explicit review path for the two Service-Only plans below. It is a
# no-op unless WALKIN_REVIEW_ENABLED=true in .env. It bypasses the member-only
# guard (is_active_member) ONLY for these two plans — the plan membership itself
# is the eligibility/consent basis (a paid same-day Walk-In / IV visit is a real
# patient encounter, unlike the free consults the member guard excludes). The
# member loop and is_active_member() are left untouched.
#
# NOTE (Hint UI config, not code): Hint's "Membership Created" (#3) automated
# welcome message must be scoped OFF for pln-jnMH3ruMbXhm and pln-qHzXwjyZ8xPP
# so these patients don't get a Hint welcome on top of the review touches.
WALKIN_REVIEW_ENABLED = os.environ.get("WALKIN_REVIEW_ENABLED", "false").lower() == "true"
WALKIN_REVIEW_PLANS = {
    "pln-jnMH3ruMbXhm",  # SO - Walk-In
    "pln-qHzXwjyZ8xPP",  # SO - IV
}
# 3-touch, engagement-gated cadence. Day offsets are measured from the FIRST
# walk-in touch (Touch 1 on the next-morning run, Touch 2 at +3d, Touch 3 at +7d).
WALKIN_TOUCH_OFFSET_DAYS = [0, 3, 7]
WALKIN_MAX_TOUCHES = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily Hint→Spruce review-request poller."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log everything but don't actually send. Overrides DRY_RUN env var.",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO 8601 timestamp to poll events from (overrides last_run_at).",
    )
    parser.add_argument(
        "--allow-patient",
        type=str,
        default=None,
        help="Only process this Hint patient ID (test mode).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help=f"If no state file, initialize last_run_at to N days ago (default {DEFAULT_LOOKBACK_DAYS}).",
    )
    return parser.parse_args()


def get_last_run_at(state: dict, lookback_days: int) -> str:
    """Return ISO 8601 timestamp for the start of the polling window."""
    meta = state.get(POLLER_STATE_KEY, {})
    last = meta.get("last_run_at")
    if last:
        return last
    fallback = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return fallback.isoformat()


def update_last_run_at(state: dict):
    state[POLLER_STATE_KEY] = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }


def fetch_interactions_since(since_iso: str) -> list:
    """
    Return list of patient_ids from Hint Clinical Interactions created since
    `since_iso`. An Interaction is created whenever the physician writes a
    note, completes a visit, or has a phone call documented — the cleanest
    visit-completed signal Hint exposes.

    Replaces the old `fetch_paid_invoices_since` trigger which was noisy in a
    concierge model (membership invoices auto-pay monthly regardless of visits).

    Hint API: GET /api/provider/interactions?created_at_after=<iso>
    """
    url = f"{HINT_BASE_URL}/api/provider/interactions"
    params = {"created_at_after": since_iso}
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        resp = http.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        interactions = data if isinstance(data, list) else data.get("data", [])
        return [i.get("patient_id") for i in interactions if i.get("patient_id")]
    except Exception as e:
        log.error(f"Failed to fetch interactions: {e}")
        return []


def active_membership_start(patient: dict):
    """Earliest start_date (YYYY-MM-DD) among the patient's ACTIVE memberships,
    or None. Used to exclude pre-enrollment free consults from review asks: a
    consult predates the membership start, a real member visit does not."""
    starts = [
        m.get("start_date")
        for m in (patient.get("memberships") or [])
        if (m.get("status") or "").lower() == "active" and m.get("start_date")
    ]
    return min(starts) if starts else None


def fetch_member_visit_patients_since(since_iso: str) -> dict:
    """Return {patient_id: latest qualifying appointment start (ISO)} for Hint
    appointments between `since_iso` and now that (a) are not cancelled and
    (b) have already occurred. This is the review trigger: a patient appears here
    if they had a real visit. Membership-start filtering (consult exclusion) is
    applied by the caller, where the patient record is available.

    Uses GET /api/provider/appointments with start_date/end_date + limit/offset
    pagination, chunked into <=30-day windows (the endpoint rejects ranges over
    31 days). Mirrors fetch_appointments() in send_daily_summary.py. Replaces the
    old /interactions trigger, which had no date field and was capped at 10 rows,
    so it silently saw only a fraction of visits. (2026-06-22)
    """
    now = datetime.now(timezone.utc)
    try:
        since_dt = datetime.fromisoformat(since_iso)
    except Exception:
        since_dt = now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)

    url = f"{HINT_BASE_URL}/api/provider/appointments"
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    out = {}
    win_start = since_dt
    while win_start < now:
        win_end = min(win_start + timedelta(days=30), now)
        offset = 0
        while True:
            params = {
                "start_date": win_start.date().isoformat(),
                "end_date": win_end.date().isoformat(),
                "limit": 100,
                "offset": offset,
            }
            try:
                resp = http.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                batch = resp.json()
                batch = batch if isinstance(batch, list) else batch.get("data", [])
            except Exception as e:
                log.error(
                    f"Failed to fetch appointments {win_start.date()}..{win_end.date()} "
                    f"offset={offset}: {e}"
                )
                break
            for a in batch:
                if (a.get("status") or "").lower() in ("cancelled", "canceled", "declined"):
                    continue
                start = a.get("start")
                if not start:
                    continue
                try:
                    if datetime.fromisoformat(start) > now:
                        continue  # future appointment — visit hasn't happened yet
                except Exception:
                    continue
                for at in (a.get("attendees") or []):
                    pid = (at.get("patient") or {}).get("id")
                    if pid and pid.startswith("pat-"):
                        if pid not in out or start > out[pid]:
                            out[pid] = start
            if len(batch) < 100:
                break
            offset += 100
        win_start = win_end + timedelta(days=1)
    return out


def hash_fname(fname: str) -> str:
    """
    SHA-256 hash of a lowercased, trimmed first name.

    The click-tracker stores hashes (not plain first names) on Cloudflare KV
    so that no consumer health data lives in third-party storage. Cloudflare
    isn't under a BAA with the practice, and Washington's My Health My Data
    Act treats "data that indicates a consumer used healthcare-related
    services" as protected. Hashing keeps the suppression behavior intact
    while removing first names from Cloudflare's blast radius entirely.

    Returns hex digest (64 chars) or empty string if fname is empty.
    """
    import hashlib
    normalized = (fname or "").strip().lower()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fetch_clicked_fname_hashes() -> set:
    """
    Fetch the set of SHA-256 hashes of first names that have clicked the
    Google review CTA on the /review page. Patients whose hashed first name
    appears get skipped — they've already engaged with the ask.

    Backed by Cloudflare Workers KV namespace REVIEW_CLICKS, which stores
    hashes only (no plain names). See src/server.ts in mbm-rebuild-43f1acd5
    for the endpoint impl and HIPAA_AUDIT.md for the design rationale.

    Returns empty set if the token isn't configured or the endpoint fails;
    that's safe-fail — we just don't suppress sends (the 3-cap still applies).
    """
    base = os.environ.get("REVIEW_BASE_URL", "https://mtbakermedical.com")
    token = os.environ.get("CLICK_TRACKER_TOKEN", "")
    if not token:
        log.warning(
            "CLICK_TRACKER_TOKEN not set — skipping click-tracker check "
            "(will not suppress already-clicked patients). "
            "Set in .env once Cloudflare Worker secret is provisioned."
        )
        return set()
    try:
        resp = http.get(
            f"{base}/api/review-clicked",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.error(
                f"click-tracker GET returned {resp.status_code}: {resp.text[:200]}"
            )
            return set()
        data = resp.json()
        records = data.get("records", [])
        # The Worker now stores and returns SHA-256 hashes (hex). For backwards
        # compatibility with any stale plain-name records from earlier testing,
        # accept both: if the value isn't a 64-char hex hash, ignore it.
        return {
            (r.get("fname") or "").strip().lower()
            for r in records
            if r.get("fname") and len((r.get("fname") or "").strip()) == 64
        }
    except Exception as e:
        log.error(f"Failed to fetch click list: {e}")
        return set()


def is_active_member(patient: dict) -> bool:
    """True only if the Hint patient is an active member.

    Review asks are member-only. A free-consult attendee who did not enroll still
    produces a Hint clinical interaction, but asking them for a Google review lacks
    the patient-relationship / ePHI-waiver consent basis the review automation
    relies on (TCPA), and risks ineligible reviews under Google policy. Added
    2026-06-10 after non-members were observed receiving review texts.
    """
    return (patient.get("membership_status") or "").lower() == "active"


def _walkin_patient_id(m: dict):
    """Patient id for a membership object. Mirrors nurture_engine._patient_of_membership:
    prefer the embedded membership_patients[0].patient.id, fall back to patient_id."""
    mps = m.get("membership_patients") or []
    if mps and isinstance(mps[0], dict):
        pid = (mps[0].get("patient") or {}).get("id")
        if pid:
            return pid
    return m.get("patient_id")


def fetch_walkin_iv_memberships_since(since_iso: str) -> list:
    """Return [{patient_id, plan_id, membership_id, created_at}] for memberships on
    the Walk-In / IV Service-Only plans created since `since_iso`.

    Uses GET /api/provider/memberships with created_at[gte] + limit/offset
    pagination (same endpoint the nurture engine polls). Filters by plan id and
    re-checks created_at client-side, defensively. Skips non-`pat-` ids
    (phantom-id guard). De-dupes to one row per patient.
    """
    url = f"{HINT_BASE_URL}/api/provider/memberships"
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except Exception:
        since_dt = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)

    out, seen, offset = [], set(), 0
    while True:
        params = {"created_at[gte]": since_dt.isoformat(), "limit": 100, "offset": offset}
        try:
            resp = http.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            batch = resp.json()
            batch = batch if isinstance(batch, list) else batch.get("data", [])
        except Exception as e:
            log.error(f"walkin-review: failed to fetch memberships offset={offset}: {e}")
            break
        for m in batch:
            plan_id = (m.get("plan") or {}).get("id")
            if plan_id not in WALKIN_REVIEW_PLANS:
                continue
            created = m.get("created_at")
            if created:
                try:
                    if datetime.fromisoformat(created.replace("Z", "+00:00")) < since_dt:
                        continue  # older than the cursor — not a new signup
                except Exception:
                    pass
            pid = _walkin_patient_id(m)
            if not pid or not pid.startswith("pat-") or pid in seen:
                continue
            seen.add(pid)
            out.append({
                "patient_id": pid, "plan_id": plan_id,
                "membership_id": m.get("id"), "created_at": created,
            })
        if len(batch) < 100:
            break
        offset += 100
    return out


def _walkin_in_progress_patients(state: dict) -> dict:
    """{patient_id: plan_id} for patients with an active (started, not finished,
    not stopped) walk-in sequence. These are the Touch 2 / Touch 3 candidates:
    their membership was created before the current cursor, so they no longer
    appear in the created-since poll — the cadence is driven from state instead.
    """
    out = {}
    for pid, entry in state.items():
        if not isinstance(pid, str) or pid.startswith("_") or not isinstance(entry, dict):
            continue
        walkin = entry.get("walkin") or {}
        if walkin.get("stopped"):
            continue
        touches = walkin.get("touches", 0)
        if 0 < touches < WALKIN_MAX_TOUCHES:
            out[pid] = walkin.get("plan_id")
    return out


def _walkin_touch_due(entry: dict, now: datetime) -> tuple:
    """Given the shared patient_state entry, decide whether a walk-in touch is due
    now. Returns (due: bool, touch_num: int, reason: str). Touch N is scheduled at
    first_touch_ts + WALKIN_TOUCH_OFFSET_DAYS[N-1]."""
    walkin = entry.get("walkin") or {}
    touches = walkin.get("touches", 0)
    if walkin.get("stopped"):
        return False, touches, f"walkin-stopped ({walkin.get('stopped')})"
    if touches >= WALKIN_MAX_TOUCHES:
        return False, touches, f"walkin-complete touches={touches}/{WALKIN_MAX_TOUCHES}"
    if touches == 0:
        return True, 1, "walkin-touch-1 due (new membership)"
    anchor = walkin.get("first_touch_ts")
    try:
        anchor_dt = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
    except Exception:
        return True, touches + 1, "walkin-touch due (no anchor)"
    due_dt = anchor_dt + timedelta(days=WALKIN_TOUCH_OFFSET_DAYS[touches])
    if now >= due_dt:
        return True, touches + 1, f"walkin-touch-{touches + 1} due"
    return False, touches, f"walkin-not-yet (next touch ~{due_dt.date()})"


def _record_walkin_touch(patient_id: str, plan_id: str, touch_num: int):
    """Record a sent walk-in touch. Updates the SHARED count / last_ask_ts (so the
    member flow's 30-day spacing dedupe sees it) AND an additive `walkin` sub-dict
    that tracks the 3-touch cadence. Old readers tolerate the extra key."""
    state = _read_patient_state()
    entry = state.get(patient_id, {"count": 0, "last_ask_ts": None})
    now_iso = datetime.now(timezone.utc).isoformat()
    entry["count"] = entry.get("count", 0) + 1
    entry["last_ask_ts"] = now_iso
    walkin = entry.get("walkin") or {}
    if not walkin.get("first_touch_ts"):
        walkin["first_touch_ts"] = now_iso
    walkin["touches"] = walkin.get("touches", 0) + 1
    walkin["last_touch_ts"] = now_iso
    walkin["plan_id"] = plan_id
    entry["walkin"] = walkin
    state[patient_id] = entry
    _write_patient_state(state)


def _dispatch_walkin_review(patient_id, first_name, email, phone, plan_id, touch_num, reason) -> bool:
    """Send one walk-in / IV review touch, replicating _dispatch_to_bridge's EXACT
    DRY_RUN guard: in dry-run, log the intent and write NO state. Returns True only
    when a touch actually went out (counted off the send helpers' bool return, never
    off a state diff — avoids the 2026-06-22 dry-run-burns-state regression)."""
    log.info(
        f"[walkin] Processing: patient_id={patient_id} plan={plan_id} "
        f"touch={touch_num}/{WALKIN_MAX_TOUCHES} "
        f"email={'yes' if email else 'no'} phone={'yes' if phone else 'no'} "
        f"dry_run={receiver.DRY_RUN} ({reason})"
    )
    email_ok = receiver.send_review_email(first_name=first_name, email=email) if email else False
    sms_ok = receiver.send_review_sms(first_name=first_name, phone=phone) if phone else False

    email_status = "ok" if email_ok else ("skip" if not email else "failed")
    sms_status = "ok" if sms_ok else ("skip" if not phone else "failed")
    log.info(f"[walkin] Delivery: email={email_status} sms={sms_status}")

    if email_ok or sms_ok:
        if receiver.DRY_RUN:
            # Dry-run must NOT mutate persistent state (send helpers return truthy
            # in dry-run). Without this guard a preview would "burn" the touch. (2026-06-22)
            log.info(
                f"[walkin] [DRY_RUN] Would record patient_id={patient_id} "
                f"touch={touch_num} (state unchanged)"
            )
        else:
            _record_walkin_touch(patient_id, plan_id, touch_num)
            new_touches = (_read_patient_state().get(patient_id, {}).get("walkin") or {}).get("touches")
            log.info(
                f"[walkin] Recorded: patient_id={patient_id} "
                f"touch={new_touches}/{WALKIN_MAX_TOUCHES}"
            )
        return True
    return False


def run_walkin_review_branch(since_iso: str, clicked_hashes: set) -> tuple:
    """Walk-In / IV review branch. Returns (sent, skipped, errors).

    STOP conditions (per touch): a Google-CTA click (click-tracker hash, forever),
    an inbound STOP (honored at the Spruce/carrier level — Spruce will not deliver
    to an opted-out number; a future inbound handler can also set walkin.stopped),
    or completion of all 3 touches. Shares patient_state.json with the member flow
    so the two paths never double-ask the same patient.
    """
    # FEATURE FLAG — default OFF. When disabled the whole branch is a no-op.
    if not WALKIN_REVIEW_ENABLED:
        log.info("walkin-review: WALKIN_REVIEW_ENABLED=false — branch disabled (no-op)")
        return 0, 0, 0

    now = datetime.now(timezone.utc)

    # Touch 1 candidates: new Walk-In / IV memberships created since the cursor.
    new_mems = fetch_walkin_iv_memberships_since(since_iso)
    plan_by_pid = {}
    for m in new_mems:
        plan_by_pid.setdefault(m["patient_id"], m["plan_id"])

    # Touch 2 / 3 candidates: in-progress sequences already recorded in state.
    state = _read_patient_state()
    for pid, plan_id in _walkin_in_progress_patients(state).items():
        plan_by_pid.setdefault(pid, plan_id)

    log.info(
        f"walkin-review: {len(new_mems)} new membership(s), "
        f"{len(plan_by_pid)} patient(s) to evaluate"
    )

    sent = skipped = errors = 0
    for pid in sorted(plan_by_pid):
        try:
            plan_id = plan_by_pid[pid]
            patient = fetch_patient(pid)
            if not patient:
                log.warning(f"walkin-review: could not fetch patient {pid} — counting as error")
                errors += 1
                continue

            # Consent gate (in-branch): electronic communication consent required.
            if patient.get("electronic_communication_consent_accepted") is not True:
                log.info(
                    f"walkin-review: SKIP patient_id={pid} — "
                    f"electronic_communication_consent_accepted != true"
                )
                skipped += 1
                continue

            phi = extract_phi_minimal(patient)
            if not phi:
                log.info(f"walkin-review: patient {pid} has no email or phone — skipping")
                skipped += 1
                continue
            first_name, email, phone = phi

            # Click-tracker suppression — already clicked the CTA => stop forever.
            fname_hash = hash_fname(first_name)
            if fname_hash and fname_hash in clicked_hashes:
                log.info(
                    f"walkin-review: SKIP patient_id={pid} fname={first_name} "
                    f"— already clicked review CTA (forever)"
                )
                skipped += 1
                continue

            # Cadence gate: which touch, and is it due yet?
            entry = _read_patient_state().get(pid, {})
            due, touch_num, reason = _walkin_touch_due(entry, now)
            if not due:
                log.info(f"walkin-review: SKIP patient_id={pid}: {reason}")
                skipped += 1
                continue

            # Shared dedupe with the member flow: never double-ask. For Touch 1,
            # honor the member flow's 30-day spacing if it already asked this
            # patient (a prior last_ask_ts with no walk-in touch yet came from the
            # member/appointment path).
            if touch_num == 1:
                last_ask_ts = entry.get("last_ask_ts")
                if last_ask_ts:
                    try:
                        last_dt = datetime.fromisoformat(last_ask_ts.replace("Z", "+00:00"))
                        if (now - last_dt).days < MIN_DAYS_BETWEEN_REQUESTS:
                            log.info(
                                f"walkin-review: SKIP patient_id={pid} — member flow "
                                f"already asked within {MIN_DAYS_BETWEEN_REQUESTS}d "
                                f"(shared spacing)"
                            )
                            skipped += 1
                            continue
                    except Exception:
                        pass

            if _dispatch_walkin_review(pid, first_name, email, phone, plan_id, touch_num, reason):
                sent += 1
            else:
                skipped += 1
        except Exception as e:
            log.error(f"walkin-review: exception processing {pid}: {e}")
            errors += 1

    log.info(f"walkin-review: done | sent={sent} skipped={skipped} errors={errors}")
    return sent, skipped, errors


def main():
    args = parse_args()
    if args.dry_run:
        # Override the receiver module's DRY_RUN flag so the existing
        # send_review_sms / send_review_email helpers honor dry-run mode.
        receiver.DRY_RUN = True

    lookback = args.lookback_days if args.lookback_days is not None else DEFAULT_LOOKBACK_DAYS
    state = _read_patient_state()
    since_iso = args.since or get_last_run_at(state, lookback)

    log.info(f"poller start: since={since_iso} dry_run={receiver.DRY_RUN}")

    if not HINT_API_KEY:
        log.error("HINT_API_KEY not set. Aborting.")
        sys.exit(2)

    # Fetch the set of already-clicked first-name HASHES ONCE up front.
    # Skip any patient whose hashed first name appears — they've engaged
    # with the ask, even if they didn't ultimately leave the review.
    # Stored as SHA-256 hashes so no plain first names live on Cloudflare KV
    # (which isn't BAA-covered). See hash_fname() for rationale.
    clicked_hashes = fetch_clicked_fname_hashes()
    log.info(f"poller: click-tracker returned {len(clicked_hashes)} hash(es) to suppress")

    # Collect patients who had a real member VISIT since `since`, from Hint
    # appointments (the cleanest "care delivered" signal). visit_dates maps
    # patient_id -> latest qualifying appointment date, used below to exclude
    # pre-enrollment free consults. Membership creation is intentionally NOT the
    # trigger: a signup before care doesn't reflect practice quality.
    visit_dates = {}
    if args.allow_patient:
        patient_ids = {args.allow_patient}
        log.info(f"poller: test mode, processing only {args.allow_patient}")
    else:
        visit_dates = fetch_member_visit_patients_since(since_iso)
        patient_ids = set(visit_dates.keys())

    log.info(f"poller: found {len(patient_ids)} patient(s) with member visits to evaluate")

    sent = 0
    skipped = 0
    errors = 0
    for pid in sorted(patient_ids):
        try:
            patient = fetch_patient(pid)
            if not patient:
                log.warning(f"poller: could not fetch patient {pid} — counting as error")
                errors += 1
                continue

            # Member-only guard (2026-06-10): only active Hint members get review
            # asks. A free-consult attendee who didn't enroll still produces a Hint
            # clinical interaction, but asking them for a Google review lacks the
            # patient-relationship / ePHI-waiver consent basis (TCPA) and risks
            # ineligible reviews under Google policy. --allow-patient test mode
            # bypasses this guard. See is_active_member().
            if not args.allow_patient and not is_active_member(patient):
                status = patient.get("membership_status") or "unknown"
                log.info(
                    f"poller: SKIP patient_id={pid} — membership_status={status} "
                    f"(review asks are active-member-only)"
                )
                skipped += 1
                continue

            # Exclude the free initial consult: only ask for visits dated on/after
            # the patient's active-membership start. A pre-enrollment consult
            # predates membership, so it's skipped — we ask only after real member
            # appointments. (2026-06-22, per Charlie.)
            if not args.allow_patient:
                mstart = active_membership_start(patient)
                appt_date = (visit_dates.get(pid) or "")[:10]
                if mstart and appt_date and appt_date < mstart:
                    log.info(
                        f"poller: SKIP patient_id={pid} — visit {appt_date} predates "
                        f"membership start {mstart} (free consult, not a member visit)"
                    )
                    skipped += 1
                    continue

            phi = extract_phi_minimal(patient)
            if not phi:
                log.info(f"poller: patient {pid} has no email or phone — skipping")
                skipped += 1
                continue
            first_name, email, phone = phi

            # Click-tracker suppression — if this patient's first-name hash
            # appears in the click-tracker, they've already engaged with the
            # ask and we don't pester them again. Hashing means no plain
            # first name lives on Cloudflare KV.
            fname_hash = hash_fname(first_name)
            if fname_hash and fname_hash in clicked_hashes:
                log.info(
                    f"poller: SKIP patient_id={pid} fname={first_name} "
                    f"— already clicked review CTA (forever)"
                )
                skipped += 1
                continue

            # _dispatch_to_bridge returns True when a request was sent (or, in
            # dry-run, WOULD be sent) and False when spacing/cap suppressed it.
            # Count off the return value so dry-run previews are accurate (it no
            # longer mutates state, so the old before/after count diff read 0).
            if _dispatch_to_bridge(pid, first_name, email, phone, trigger="poller"):
                sent += 1
            else:
                skipped += 1
        except Exception as e:
            log.error(f"poller: exception processing {pid}: {e}")
            errors += 1

    # ── Walk-In / IV review branch (feature-flagged; default OFF) ─────────────
    # Separate, explicit path for the two Service-Only plans. No-op unless
    # WALKIN_REVIEW_ENABLED=true. Shares patient_state.json + the click-hash set
    # with the member flow, and folds its errors into the shared counter so the
    # cursor only advances when BOTH the member loop and this branch ran clean.
    # Cadence updated to 3-touch, overriding WALKIN_REVIEW_WIRING_DESIGN.md's
    # single-ask design.
    w_sent, w_skipped, w_errors = run_walkin_review_branch(since_iso, clicked_hashes)
    sent += w_sent
    skipped += w_skipped
    errors += w_errors

    # Only advance last_run_at on a clean run, so any failed events get retried
    # on the next poll cycle.
    state = _read_patient_state()
    if errors == 0:
        update_last_run_at(state)
        _write_patient_state(state)
        log.info(
            f"poller end: ok | sent={sent} skipped={skipped} errors={errors} | last_run_at advanced"
        )
        sys.exit(0)
    else:
        log.warning(
            f"poller end: partial | sent={sent} skipped={skipped} errors={errors} | last_run_at NOT advanced (will retry next run)"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
