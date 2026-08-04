#!/usr/bin/env python3
"""Nightly pre-visit outside-records pull (Mt. Baker Medical).

For every patient on TOMORROW's Hint schedule, search the HIE via Abstractive
and drop the summary, the source-document ZIP and a paste-ready .md into that
patient's Drive folder, so the records are waiting before the visit.

SCOPE (deliberate): keyed to actual scheduled appointments. Querying an HIE is
permitted for treatment; a patient on tomorrow's schedule qualifies. Do NOT
widen this to the whole panel "to have it on file" - Carequality participation
is purpose-bound.

PHI: runs on the practice machine only. The log records COUNTS and a truncated
hash of each patient id - never a name, DOB, or contact detail - so it can be
reviewed from outside the covered-entity boundary.

Gates: ABSTRACTIVE_ENABLED=true AND ABSTRACTIVE_PREFETCH_ENABLED=true.
--dry-run needs neither and calls no APIs beyond Hint (lists who it WOULD pull).

  py abstractive_prefetch.py --dry-run
  py abstractive_prefetch.py
  py abstractive_prefetch.py --days 2 --repull-days 30 --no-zip
"""
import argparse
import hashlib
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import abstractive_client as ac

try:
    import abstractive_render as ar
except Exception:
    ar = None

HERE = Path(__file__).parent
STATE_FILE = HERE / "abstractive_prefetch_state.json"
LOG_FILE = HERE / "abstractive_prefetch.log"
PAUSE_BETWEEN_PATIENTS_S = 60
TEST_NAME_PREFIXES = ("test", "ztest", "cowork")

log = logging.getLogger("prefetch")


def setup_log():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def tag(patient_id):
    """Stable, non-identifying handle for logs."""
    return hashlib.sha256(patient_id.encode()).hexdigest()[:8]


# --- Hint ---------------------------------------------------------------------

def hint_get(env, path, params=None):
    base = ("https://api.hint.com" if env.get("HINT_ENV") == "production"
            else "https://api.sandbox.hint.com")
    url = f"{base}{path}"
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {env['HINT_API_KEY']}",
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def appointments_for(env, day):
    """All appointments on `day` (a date). Paged.

    Hint's end_date is EXCLUSIVE - verified 2026-08-03: start=end=2026-08-04
    returned 0 while that day demonstrably had 3 appointments, and
    start=2026-08-04/end=2026-08-05 returned exactly those 3. So ask for
    [day, day+1).
    """
    out, offset, limit = [], 0, 100
    end = (day + timedelta(days=1)).isoformat()
    while True:
        rows = hint_get(env, "/api/provider/appointments",
                        {"start_date": day.isoformat(), "end_date": end,
                         "limit": limit, "offset": offset})
        if isinstance(rows, dict):
            rows = rows.get("appointments") or rows.get("data") or []
        out.extend(rows)
        if len(rows) < limit:
            return out
        offset += limit


def patient_ids_from(appts):
    """Existing patients only. An attendee with no patient id is a prospect
    (Hint keeps them as a Contact until enrollment) - nothing to look up."""
    ids = []
    for a in appts:
        for att in (a.get("attendees") or []):
            pid = ((att.get("patient") or {}).get("id"))
            if pid and pid not in ids:
                ids.append(pid)
    return ids


def demographics(env, patient_id):
    """Map a Hint patient record onto the Abstractive demographics block."""
    p = hint_get(env, f"/api/provider/patients/{patient_id}")
    first = (p.get("first_name") or "").strip()
    last = (p.get("last_name") or "").strip()
    dob = (p.get("dob") or "").replace("-", "")          # Hint YYYY-MM-DD
    sex = (p.get("sex") or p.get("gender") or "").strip().lower()
    gender = {"male": "M", "female": "F", "m": "M", "f": "F"}.get(sex)
    if not (first and last and len(dob) == 8 and gender):
        return None, f"incomplete demographics (dob={bool(dob)} sex={bool(gender)})"
    if first.lower().startswith(TEST_NAME_PREFIXES) or last.lower().startswith(TEST_NAME_PREFIXES):
        return None, "test account"
    return {
        "first": first, "last": last, "dob": dob, "gender": gender,
        "street": (p.get("address_line1") or "").strip() or None,
        "city": (p.get("address_city") or "").strip() or None,
        "state": (p.get("address_state") or "").strip() or None,
        "zip": (p.get("address_zip") or "").strip() or None,
    }, None


# --- state --------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("state file unreadable - starting fresh")
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pulled_recently(state, pid, days):
    ts = state.get(pid)
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - when) < timedelta(days=days)


# --- main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1,
                    help="days ahead to prep (1 = tomorrow, default)")
    ap.add_argument("--repull-days", type=int, default=30,
                    help="skip a patient pulled within this many days (default 30)")
    ap.add_argument("--no-zip", action="store_true",
                    help="summary only, skip the ~45MB document ZIP")
    ap.add_argument("--dry-run", action="store_true",
                    help="list who would be pulled; calls no Abstractive API")
    a = ap.parse_args()

    setup_log()
    env = ac.load_env()
    for k in ("HINT_API_KEY",):
        if not env.get(k):
            sys.exit(f"missing {k} in .env")

    day = date.today() + timedelta(days=a.days)
    log.info(f"prefetch start: target={day.isoformat()} dry_run={a.dry_run} "
             f"repull_days={a.repull_days} zip={not a.no_zip}")

    if not a.dry_run:
        if env.get("ABSTRACTIVE_ENABLED", "false").lower() != "true":
            sys.exit("BLOCKED: ABSTRACTIVE_ENABLED is not true.")
        if env.get("ABSTRACTIVE_PREFETCH_ENABLED", "false").lower() != "true":
            sys.exit("BLOCKED: ABSTRACTIVE_PREFETCH_ENABLED is not true. "
                     "Set it in .env when you're ready for this to run live.")

    try:
        appts = appointments_for(env, day)
    except urllib.error.HTTPError as e:
        sys.exit(f"Hint /appointments failed: HTTP {e.code}")
    ids = patient_ids_from(appts)
    log.info(f"appointments={len(appts)} distinct_patients={len(ids)}")

    state = load_state()
    todo = []
    for pid in ids:
        if pulled_recently(state, pid, a.repull_days):
            log.info(f"  skip {tag(pid)}: pulled within {a.repull_days}d")
            continue
        try:
            demo, why = demographics(env, pid)
        except urllib.error.HTTPError as e:
            log.error(f"  skip {tag(pid)}: patient fetch HTTP {e.code}")
            continue
        if not demo:
            log.info(f"  skip {tag(pid)}: {why}")
            continue
        todo.append((pid, demo))

    log.info(f"to pull: {len(todo)}")
    if a.dry_run:
        for pid, demo in todo:
            has_addr = all([demo["street"], demo["city"], demo["state"], demo["zip"]])
            log.info(f"  WOULD PULL {tag(pid)} (full address: {has_addr})")
        log.info("dry run: no Abstractive calls made")
        return

    if not todo:
        log.info("prefetch end: nothing to do")
        return

    token = ac.get_token(env)          # one token covers the batch (60 min)
    root = ac.out_root(env)
    ok = fail = 0
    for i, (pid, demo) in enumerate(todo):
        if i:
            time.sleep(PAUSE_BETWEEN_PATIENTS_S)
        log.info(f"  pulling {tag(pid)} ({i + 1}/{len(todo)})")
        try:
            res = ac.run_search(env, demo, root=root, want_zip=not a.no_zip,
                                token=token, log=lambda m: log.info(f"    {m}"))
        except Exception as e:
            fail += 1
            log.error(f"  {tag(pid)} crashed: {type(e).__name__}: {e}")
            continue
        if not res.get("ok"):
            fail += 1
            log.error(f"  {tag(pid)} failed: {res.get('error')}")
            continue
        if res.get("summary") and ar:
            try:
                out, sections, nsec = ar.render(res["summary"])
                log.info(f"    rendered {sections} sections from {nsec} documents")
            except Exception as e:
                log.error(f"    render failed: {type(e).__name__}: {e}")
        ok += 1
        state[pid] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    log.info(f"prefetch end: pulled={ok} failed={fail}")


if __name__ == "__main__":
    main()
