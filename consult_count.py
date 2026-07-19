#!/usr/bin/env python3
"""Point-in-time free-consult counter for the MBM daily digest. Hint erases the Contact
attendee when a prospect enrolls, so a converted consult looks like a member visit after the
fact. We capture consults while still Contacts each run and persist a running monthly tally
that never decays. State file consult_count_state.json is runtime-only (do NOT commit).

MBM BOOK SOURCE (2026-07-17, ADDITIVE, gated by GCAL_ENABLED):
  mbm-book web bookings from /book-beta exist ONLY as Google Calendar events (Hint's
  appointments API is read-only and never sees them), so the Contact-attendee rule below
  can never count them. When GCAL_ENABLED=true, tally() ALSO folds mbm-book GCal events
  into the same never-decaying map, keyed "gcal:<event_id>" (dedup by event id, cancelled
  skipped). With GCAL_ENABLED unset/false this file behaves byte-for-byte as before. The
  read is read-only and never writes anything except the local tally state file.
"""
import os
import json
import pathlib
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env")
except ImportError:
    pass

STATE_FILE = pathlib.Path(__file__).parent / "consult_count_state.json"
DEAD = {"cancelled", "canceled", "declined"}

# mbm-book GCal source config (read at CALL time to survive import-order vs dotenv).
GCAL_SOURCE_LOOKBACK_MIN_DAYS = 35   # floor; widened to cover the whole month below


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


def _gcal_enabled():
    return os.environ.get("GCAL_ENABLED", "false").lower() == "true"


def _merge_gcal_bookings(st, month_start, daily_end):
    """ADDITIVE, gated. Fold mbm-book Google Calendar bookings created this month into
    the persistent `counted` map under "gcal:<event_id>" keys (dedup by event id;
    cancelled skipped). No-op unless GCAL_ENABLED and both GCAL_CALENDAR_ID +
    GOOGLE_SA_KEY_FILE are set. Read-only against Google; the only write is the local
    tally state. A hard calendar read failure leaves the tally untouched (never zeros)."""
    if not _gcal_enabled():
        return
    cal = os.environ.get("GCAL_CALENDAR_ID", "")
    sa = os.environ.get("GOOGLE_SA_KEY_FILE", "")
    if not (cal and sa):
        return
    try:
        import gcal_bookings  # lazy: only import (and pull requests/cryptography) when on
    except Exception:
        return
    now = datetime.now(timezone.utc)
    try:
        span_days = (now - month_start).days + 2
    except Exception:
        span_days = GCAL_SOURCE_LOOKBACK_MIN_DAYS
    lookback = max(GCAL_SOURCE_LOOKBACK_MIN_DAYS, span_days)
    # lookahead_days must span the BOOKING HORIZON, not 1 day. Consults are booked for
    # FUTURE dates (days/weeks out) and Google's events.list filters by event START time,
    # so a consult booked TODAY for e.g. Jul 27 is invisible under the default 1-day
    # lookahead. 120d covers any realistic consult horizon; the month filter below still
    # keys on booked_at, so only THIS MONTH's bookings are counted regardless of start.
    events = gcal_bookings.fetch_mbm_book_events(now, cal, sa, lookback, lookahead_days=120)
    if not events:  # None (hard failure) or [] (nothing) -> leave the tally as-is
        return
    counted = st["counted"]
    for ev in events:
        if not isinstance(ev, dict) or ev.get("status") == "cancelled":
            continue
        eid = ev.get("id")
        if not eid:
            continue
        priv = ((ev.get("extendedProperties") or {}).get("private") or {})
        raw = priv.get("booked_at") or ev.get("created") or ((ev.get("start") or {}).get("dateTime"))
        created = _parse_dt(raw)
        if created is None or not (month_start <= created < daily_end):
            continue
        counted["gcal:" + eid] = created.isoformat()   # idempotent: re-seeing an id is a no-op
    st["counted"] = counted


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

    ADDITIVE second source (gated GCAL_ENABLED): mbm-book GCal web bookings are folded into
    the same map under "gcal:<event_id>" keys. They are a DISJOINT source from the Hint
    Contact-attendee consults (an mbm-book booking is never a Hint appointment), so no
    double-count with the appts loop below.

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
    # ADDITIVE: fold in mbm-book GCal bookings (gated; no-op when GCAL_ENABLED off).
    # Mutates st["counted"] in place (same dict as `counted`), so the derivations below
    # naturally include them.
    _merge_gcal_bookings(st, month_start, daily_end)
    _save(st)
    window = 0
    for iso in counted.values():
        d = _parse_dt(iso)
        if d is not None and daily_start <= d < daily_end:
            window += 1
    return window, len(counted)
