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
        st = json.loads(STATE_FILE.read_text())
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}
    st.setdefault("month", None)
    # `counted` maps {appointment_id: created_at_iso}. Older state files stored a bare
    # `counted_ids` list with no dates -- migrate those in with a null date so the MTD
    # total is preserved; each one's date heals the next time a run re-fetches it.
    if not isinstance(st.get("counted"), dict):
        st["counted"] = {aid: None for aid in (st.get("counted_ids") or [])}
    return st


def _save(st):
    try:
        counted = st.get("counted") or {}
        st["counted_ids"] = sorted(counted)   # kept for older readers
        st["mtd_count"] = len(counted)        # DERIVED from the map, never blind-incremented
        STATE_FILE.write_text(json.dumps(st, indent=2))
    except Exception:
        pass


def tally(appts, daily_start, daily_end, month_start):
    """Persist every consult booking created this month as {id: created_at}, then DERIVE both
    the window count and the month-to-date count from that map.

    Why a map instead of a counter + seen-set: the previous version incremented mtd_count and
    skipped any id it had already seen -- and it did that skip BEFORE the window check. So a
    second run covering the same appointments (e.g. a daily tally-keeper running alongside the
    weekly digest) would consume the ids, and the weekly digest would then report ~0 for its
    period while MTD kept climbing. Deriving both numbers from the persisted map makes tally
    idempotent: it can run on any cadence, any number of times, and both counts stay correct.

    Still never decays: once an id is counted it stays counted, which is the whole point --
    Hint erases the Contact attendee on enrollment, so a converted consult stops looking like
    a consult and would otherwise vanish from the month retroactively.

    Returns (window_count, mtd_count).
    """
    month_key = month_start.strftime("%Y-%m")
    st = _load()
    if st.get("month") != month_key:
        st = {"month": month_key, "counted": {}}
    counted = st["counted"]
    for a in appts:
        aid = a.get("id")
        if not aid:
            continue
        if (a.get("status") or "").lower() in DEAD:
            continue
        if not is_consult_booking(a):
            continue
        created = _parse_dt(a.get("created_at"))
        if created is None or not (month_start <= created < daily_end):
            continue
        counted[aid] = created.isoformat()   # idempotent: re-seeing a known id is a no-op
    st["counted"] = counted
    _save(st)
    window = 0
    for iso in counted.values():
        d = _parse_dt(iso)
        if d is not None and daily_start <= d < daily_end:
            window += 1
    return window, len(counted)
