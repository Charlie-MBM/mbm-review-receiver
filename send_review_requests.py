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
import sys
from datetime import datetime, timedelta, timezone

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


def fetch_paid_invoices_since(since_iso: str) -> list:
    """
    Return list of patient_ids from invoices paid since `since_iso`.

    NOTE: Hint API parameter names (paid_at_after, status filter) and response
    envelope (list vs {"data": [...]}) may need adjustment based on actual Hint
    API docs. Verify against https://docs.hint.com when the API key is configured.
    """
    url = f"{HINT_BASE_URL}/api/provider/customer_invoices"
    params = {"paid_at_after": since_iso, "status": "paid"}
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        resp = http.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        invoices = data if isinstance(data, list) else data.get("data", [])
        return [inv.get("patient_id") for inv in invoices if inv.get("patient_id")]
    except Exception as e:
        log.error(f"Failed to fetch paid invoices: {e}")
        return []


def fetch_memberships_created_since(since_iso: str) -> list:
    """
    Return list of patient_ids from memberships created since `since_iso`.

    Same Hint API caveat as fetch_paid_invoices_since.
    """
    url = f"{HINT_BASE_URL}/api/provider/memberships"
    params = {"created_at_after": since_iso}
    headers = {"Authorization": f"Bearer {HINT_API_KEY}"}
    try:
        resp = http.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        memberships = data if isinstance(data, list) else data.get("data", [])
        return [m.get("patient_id") for m in memberships if m.get("patient_id")]
    except Exception as e:
        log.error(f"Failed to fetch memberships: {e}")
        return []


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

    # Collect unique patient_ids from both event types
    patient_ids = set()
    if args.allow_patient:
        patient_ids.add(args.allow_patient)
        log.info(f"poller: test mode, processing only {args.allow_patient}")
    else:
        for pid in fetch_memberships_created_since(since_iso):
            patient_ids.add(pid)
        for pid in fetch_paid_invoices_since(since_iso):
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
            phi = extract_phi_minimal(patient)
            if not phi:
                log.info(f"poller: patient {pid} has no email or phone — skipping")
                skipped += 1
                continue
            first_name, email, phone = phi
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
