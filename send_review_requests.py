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
)

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

POLLER_STATE_KEY = "_poller_meta"
DEFAULT_LOOKBACK_DAYS = 1


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

    # Collect unique patient_ids from Clinical Interactions only — the cleanest
    # post-visit signal. Membership creation as a trigger was dropped because
    # (a) the patient may not have actually been seen yet, (b) reviews after a
    # signup form but before care don't reflect the practice's quality, and
    # (c) it ties consent more tightly to "real care has been delivered".
    patient_ids = set()
    if args.allow_patient:
        patient_ids.add(args.allow_patient)
        log.info(f"poller: test mode, processing only {args.allow_patient}")
    else:
        for pid in fetch_interactions_since(since_iso):
            patient_ids.add(pid)

    log.info(f"poller: found {len(patient_ids)} unique patient_id(s) to evaluate")

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

            before_count = _read_patient_state().get(pid, {}).get("count", 0)
            _dispatch_to_bridge(pid, first_name, email, phone, trigger="poller")
            after_count = _read_patient_state().get(pid, {}).get("count", 0)
            if after_count > before_count:
                sent += 1
            else:
                skipped += 1
        except Exception as e:
            log.error(f"poller: exception processing {pid}: {e}")
            errors += 1

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
