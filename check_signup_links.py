#!/usr/bin/env python3
"""check_signup_links.py - verify every Hint membership-signup URL still resolves.

WHY: the nurture poller sends prospects / pending members a plan-specific Hint
signup link from nurture_engine.PLAN_SIGNUP_URLS
(plan_id -> https://mtbakermedical.hint.com/signup/<slug>). If Hint ever renames
a plan slug, that link silently 404s and we'd text a patient a dead enrollment
link. This checks each URL (follows redirects, expects HTTP 200) and reports -
and optionally Spruce-alerts - any that fail.

The link list is IMPORTED from nurture_engine, so this check can never drift from
what the poller actually sends. Read-only: it loads public signup pages, submits
nothing.

Usage (run from the mbm-review-receiver folder):
  py check_signup_links.py            # human-readable report; exit 1 if any dead
  py check_signup_links.py --alert    # also Spruce-text ALERT_PHONE on any failure

Suggested: a weekly scheduled task running `--alert` so a slug change is caught
before a patient hits the dead link (see MBM pollers runbook to register it).
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# Single source of truth: the exact map the nurture poller sends from.
try:
    import nurture_engine as ne
    LINKS = dict(ne.PLAN_SIGNUP_URLS)
except Exception as e:  # noqa: BLE001
    print(f"FATAL: could not import PLAN_SIGNUP_URLS from nurture_engine: {e}")
    sys.exit(2)

# Alert config read straight from env (NOT via nurture_engine.spruce_send_sms,
# which is DRY_RUN-gated - we always want the dead-link alert to actually send).
# Prefer an explicit ALERT_PHONE; otherwise reuse SUMMARY_SMS_TO (the number the
# daily-summary poller already texts) so this works with the existing .env and
# never needs a separate secret.
ALERT_PHONE = os.environ.get("ALERT_PHONE", "") or os.environ.get("SUMMARY_SMS_TO", "")
SPRUCE_API_KEY = os.environ.get("SPRUCE_API_KEY", "")
SPRUCE_INTERNAL_ENDPOINT_ID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
SPRUCE_BASE_URL = "https://api.sprucehealth.com/v1"

TIMEOUT = 20
UA = {"User-Agent": "mbm-signup-link-check/1.0"}


def check_one(url):
    """(ok, status). ok == final response is HTTP 200 after redirects."""
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True, headers=UA)
        return (r.status_code == 200), r.status_code
    except Exception as e:  # noqa: BLE001
        return False, f"ERR {type(e).__name__}"


def send_alert(dead):
    """Directly POST a Spruce SMS to ALERT_PHONE (bypasses the DRY_RUN gate)."""
    if not ALERT_PHONE:
        print("  (no ALERT_PHONE set - skipping SMS alert)")
        return
    if not (SPRUCE_API_KEY and SPRUCE_INTERNAL_ENDPOINT_ID):
        print("  (Spruce not configured - skipping SMS alert)")
        return
    slugs = ", ".join(u.rstrip("/").rsplit("/", 1)[-1] for _, u, _ in dead)
    body = (f"MBM link check: {len(dead)} Hint signup link(s) are DEAD ({slugs}). "
            f"The nurture poller may be texting patients a broken enrollment link - "
            f"fix the slug(s) in nurture_engine.PLAN_SIGNUP_URLS.")
    try:
        r = requests.post(
            f"{SPRUCE_BASE_URL}/internalendpoints/{SPRUCE_INTERNAL_ENDPOINT_ID}/conversations",
            headers={"Authorization": f"Bearer {SPRUCE_API_KEY}",
                     "Content-Type": "application/json"},
            json={"destination": {"smsOrEmailEndpoint": ALERT_PHONE},
                  "message": {"body": [{"type": "text", "value": body}]}},
            timeout=TIMEOUT,
        )
        print(f"  alert -> {'sent' if r.status_code in (200, 201) else f'FAILED {r.status_code}'}")
    except Exception as e:  # noqa: BLE001
        print(f"  alert send failed: {e}")


def main():
    alert = "--alert" in sys.argv
    print(f"Checking {len(LINKS)} Hint signup links...\n")
    dead = []
    for plan_id, url in sorted(LINKS.items(), key=lambda kv: kv[1]):
        ok, status = check_one(url)
        print(f"  [{'OK ' if ok else 'DEAD'}] {str(status):>4}  {url}  ({plan_id})")
        if not ok:
            dead.append((plan_id, url, status))

    print()
    if not dead:
        print(f"All {len(LINKS)} signup links resolve (HTTP 200). Nothing to do.")
        return 0

    print(f"WARNING: {len(dead)} signup link(s) NOT resolving:")
    for plan_id, url, status in dead:
        print(f"   {status}  {url}  ({plan_id})")
    if alert:
        send_alert(dead)
    return 1


if __name__ == "__main__":
    sys.exit(main())
