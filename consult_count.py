#!/usr/bin/env python3
"""Point-in-time free-consult counter for the MBM daily digest. Hint erases the Contact
attendee when a prospect enrolls, so a converted consult looks like a member visit after the
fact. We capture consults while still Contacts each run and persist a running monthly tally
that never decays. State file consult_count_state.json is runtime-only (do NOT commit)."""
import json
import pathlib
from datetime import datetime, timezone

STATE_FILE = pathlib.Path(__file__).parent / "consult_count_state.json"
DEAD = {"cancelled", "canceled", "declined"}


def _parse_dt(s):
    if not s:
        return None
    try:
        s = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            return None


def is_consult_booking(a):
    for att in (a.get("attendees") or []):
        if (att.get("type") or "").lower() == "staff":
            continue
        return ((att.get("patient") or {}) or {}).get("id") is None
    return False


def _load():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"month": None, "mtd_count": 0, "counted_ids": []}


def _save(st):
    try:
        STATE_FILE.write_text(json.dumps(st, indent=2))
    except Exception:
        pass


def tally(appts, daily_start, daily_end, month_start):
    month_key = month_start.strftime("%Y-%m")
    st = _load()
    if st.get("month") != month_key:
        st = {"month": month_key, "mtd_count": 0, "counted_ids": []}
    counted = set(st.get("counted_ids") or [])
    daily = 0
    for a in appts:
        aid = a.get("id")
        if not aid or aid in counted:
            continue
        if (a.get("status") or "").lower() in DEAD:
            continue
        if not is_consult_booking(a):
            continue
        created = _parse_dt(a.get("created_at"))
        if created is None or not (month_start <= created < daily_end):
            continue
        counted.add(aid)
        st["mtd_count"] = int(st.get("mtd_count", 0)) + 1
        if daily_start <= created < daily_end:
            daily += 1
    st["counted_ids"] = sorted(counted)
    _save(st)
    return daily, st["mtd_count"]
