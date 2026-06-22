"""
check_poller_health.py - watchdog for the MBM review poller.

The review poller writes _poller_meta.last_run_at to patient_state.json on every
clean run. If that timestamp goes stale (task disabled, command broken, or the
poller erroring out so it never advances the cursor), this watchdog texts/emails
Charlie. It runs as its OWN scheduled task (MBM-Poller-Watchdog) so it still
fires when the review poller itself is dead - exactly the failure that went
unnoticed for ~13 days in June 2026.

Reuses send_daily_summary's tested Spruce SMS + Resend email senders, so the
alert goes to the same line/inbox as the daily ops digest.

Exit codes: 0 healthy, 1 stale (alert sent), 2 could not read state (alert sent).

Run:  py check_poller_health.py
Env:  POLLER_MAX_AGE_HOURS  (default 26) - alert if last clean run is older.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Load .env BEFORE importing send_daily_summary, whose Spruce/Resend config is
# read from os.environ at import time (same pattern as send_review_requests.py).
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except Exception:
    pass

import send_daily_summary as sds  # noqa: E402  (must follow load_dotenv)

STATE_PATH = Path(__file__).parent / "patient_state.json"
MAX_AGE_HOURS = float(os.environ.get("POLLER_MAX_AGE_HOURS", "26"))


def read_last_run():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data.get("_poller_meta", {}).get("last_run_at")
    except Exception:
        return None


def _alert(subject, msg):
    sds.log(msg)
    sds.send_sms(msg)
    sds.send_email(subject, msg, f"<p>{msg}</p>")


def main():
    now = datetime.now(timezone.utc)
    last_run = read_last_run()

    if not last_run:
        _alert(
            "MBM review poller DOWN (watchdog)",
            "MBM review poller WATCHDOG: cannot read last_run_at from "
            "patient_state.json. The poller may be down or the state file missing. "
            "Check the MBM-Review-Poller task and review_cron.log.",
        )
        sys.exit(2)

    try:
        ts = datetime.fromisoformat(last_run)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (now - ts).total_seconds() / 3600.0
        age_desc = f"last clean run {age_h:.1f}h ago ({last_run})"
        stale = age_h > MAX_AGE_HOURS
    except Exception:
        age_desc = f"unparseable last_run_at {last_run!r}"
        stale = True

    if stale:
        _alert(
            "MBM review poller may be DOWN (watchdog)",
            f"MBM review poller WATCHDOG ALERT: {age_desc} (threshold "
            f"{MAX_AGE_HOURS:.0f}h). The review-request poller has not completed a "
            f"clean run recently. Check the MBM-Review-Poller scheduled task and "
            f"review_cron.log.",
        )
        sys.exit(1)

    sds.log(f"review poller healthy: {age_desc} (threshold {MAX_AGE_HOURS:.0f}h)")
    sys.exit(0)


if __name__ == "__main__":
    main()
