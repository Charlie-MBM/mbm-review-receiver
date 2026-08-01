#!/usr/bin/env python3
"""Why does consults.booked_mtd_running_tally read 23 while scheduled_mtd reads 8?

Run from mbm-review-receiver:   py _diag_consult_tally_20260730.py
State-file only, no network:    py _diag_consult_tally_20260730.py --offline

READ-ONLY. This script never writes a file, never PATCHes, never sends anything.
It opens consult_count_state.json and makes read-only GETs to Google Calendar and
Hint.

WHY THIS EXISTS
The dashboard shows two consult numbers that disagree by ~3x, and every time one of
us looks at it we re-derive the same reasoning from scratch. They are not the same
metric. Reading the source (consult_count.py, gcal_bookings.py,
export_dashboard_members.py) establishes four structural differences:

  SOURCE    the running tally folds in mbm-book GCal bookings under "gcal:<id>"
            keys. scheduled_mtd is Hint-appointments-only, and Hint's API never
            sees a /book-beta booking. Since the 2026-07-18 cutover essentially
            all web bookings are GCal-only, so a large slice of the tally is
            structurally invisible to scheduled_mtd.
  BASIS     the tally windows on created_at / booked_at (when the booking was
            MADE). scheduled_mtd windows on the appointment START date. A consult
            booked Jul 28 for Aug 5 is in the tally and not in scheduled_mtd.
  DECAY     the tally never decays. scheduled_mtd is a fresh recount every run.
  CANCELLED scheduled_mtd excludes cancelled. The tally keeps anything it counted
            before the cancellation happened.

Those four are BY DESIGN and mostly correct. Never-decay exists because Hint
erases the Contact attendee when a consult converts to a patient -- a converted
consult still happened, and a recount would lose it.

But never-decay also permanently absorbs two things that were never consults:

  (a) CANCELLED / DELETED mbm-book bookings. fetch_mbm_book_events passes
      showDeleted:"false", and the cancellation watcher detects a cancellation by
      the event DISAPPEARING. So once an event is gone the tally has no mechanism
      -- none, at all -- to remove it.
  (b) TEST bookings (the known stale "Free Consult (GLP-1) - Test" on Jul 27,
      plus Testalpha / Testbeta).

That is a real over-count with no self-correction. This script measures how big it
actually is instead of us guessing.

PHI SAFETY -- the output of this script is designed to be paste-safe.
It prints counts, day-level dates, status strings, HTTP status codes and FIELD
NAMES. It never prints a patient name, a phone number, an appointment id, a GCal
event id, or a GCal event summary. GCal summaries are read in memory (they carry
patient first names, e.g. "Free Consult (GLP-1) - First") purely to count
test-looking events; they are never printed, logged, or returned.

Everything external is wrapped. A failing phase degrades to "n/a" and the rest
still runs.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# From export_dashboard_members.py -- substrings that mark an appointment dead.
CANCELLED_MARKERS = ("cancel", "declin", "no_show", "no-show", "noshow", "reschedul")

# Probed in order; the first one present on the appointment objects wins.
# THIS ORDER IS COPIED VERBATIM from export_dashboard_members.py:943 (START_KEYS)
# and must stay in lockstep with it. If this script probed a different order and an
# appointment carried two of these fields, we would silently window on a different
# date than the dashboard does and then blame the discrepancy on the data.
START_FIELD_CANDIDATES = (
    "start_at", "starts_at", "start", "start_time",
    "scheduled_at", "date", "start_date",
)
CREATED_FIELD_CANDIDATES = ("created_at", "created", "booked_at", "inserted_at")
STATUS_FIELD_CANDIDATES = ("status", "state", "appointment_status")

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def rule(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def day_of(v):
    """Best-effort 'YYYY-MM-DD' out of whatever date shape Hint/GCal handed us."""
    if not v:
        return None
    if isinstance(v, dict):  # GCal {"dateTime": ...} / {"date": ...}
        v = v.get("dateTime") or v.get("date")
    if not isinstance(v, str):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    return m.group(1) if m else None


def is_dead(a, status_field):
    s = ""
    if status_field:
        s = str(a.get(status_field) or "").lower()
    if not s:
        for f in STATUS_FIELD_CANDIDATES:
            s = str(a.get(f) or "").lower()
            if s:
                break
    return any(m in s for m in CANCELLED_MARKERS)


# --------------------------------------------------------------------------
# PHASE 1 -- local state file. No network.
# --------------------------------------------------------------------------
def phase1(month_key):
    rule("PHASE 1 -- consult_count_state.json (local, no network)")

    state_path = HERE / "consult_count_state.json"
    try:
        import consult_count as cc
        sf = getattr(cc, "STATE_FILE", None)
        if sf:
            state_path = Path(sf) if os.path.isabs(str(sf)) else HERE / str(sf)
    except Exception as e:
        print(f"  (could not import consult_count: {e.__class__.__name__}; "
              f"assuming ./consult_count_state.json)")

    if not state_path.exists():
        print(f"  *** STATE FILE MISSING: {state_path.name}")
        print("  The tally cannot advance without it. If this is unexpected, the")
        print("  poller has never run in this checkout, or the file was cleaned up.")
        return None

    age_days = (datetime.now() - datetime.fromtimestamp(state_path.stat().st_mtime)).days
    print(f"  file: {state_path.name}   last modified {age_days} day(s) ago")
    if age_days >= 2:
        print("  *** STALE (>=2 days). The poller has stopped. The tally is frozen,")
        print("  *** so it is UNDER-counting, and every number below is as of then.")

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  *** unreadable: {e.__class__.__name__}: {e}")
        return None

    # Key NAMES are safe to print; values are not.
    print(f"  top-level keys: {sorted(raw.keys())}")

    file_month = None
    for k, v in raw.items():
        if isinstance(v, str) and MONTH_RE.match(v):
            file_month = v
            print(f"  month key: {k!r} = {v}")
            break
    if file_month is None:
        print("  month key: not found (no top-level 'YYYY-MM' string)")
    elif file_month != month_key:
        print(f"  *** MONTH MISMATCH: state says {file_month}, today is {month_key}.")
        print("  *** tally() resets the map on a month change, so the tally you are")
        print("  *** looking at on the dashboard is LAST month's until the poller runs.")

    counted = raw.get("counted")
    if not isinstance(counted, dict):
        for k, v in raw.items():
            if isinstance(v, dict) and v:
                counted = v
                print(f"  (no 'counted' key; using {k!r} as the counted map)")
                break
    if not isinstance(counted, dict):
        print("  *** could not locate the counted map. Stopping phase 1.")
        return None

    n = len(counted)
    mtd = raw.get("mtd_count")
    print(f"  counted entries: {n}")
    print(f"  mtd_count field: {mtd}")
    if isinstance(mtd, int) and mtd != n:
        print(f"  *** DISAGREEMENT: mtd_count={mtd} but len(counted)={n}. _save() is")
        print("  *** supposed to derive one from the other, so this means something")
        print("  *** wrote mtd_count directly. That is a bug worth chasing.")
    else:
        print("  mtd_count agrees with len(counted).  (good -- derived, not incremented)")

    gcal_keys = [k for k in counted if str(k).startswith("gcal:")]
    hint_keys = [k for k in counted if not str(k).startswith("gcal:")]
    print()
    print(f"  SOURCE SPLIT     gcal (mbm-book): {len(gcal_keys)}"
          f"    hint appointments: {len(hint_keys)}")
    print("  Only the hint half is even eligible to appear in scheduled_mtd.")

    nulls = sum(1 for v in counted.values() if not (v.get("booked_at") if isinstance(v, dict) else v))
    if nulls:
        print(f"  entries with no booked_at date: {nulls} "
              f"(legacy rows; invisible to the histogram below)")

    hist = Counter()
    out_of_month = 0
    for v in counted.values():
        d = day_of(v.get("booked_at") if isinstance(v, dict) else v)
        if not d:
            continue
        if d.startswith(month_key):
            hist[d] += 1
        else:
            out_of_month += 1
    if hist:
        print()
        print("  booked_at by day (this month):")
        for d in sorted(hist):
            print(f"    {d}  {'#' * hist[d]} {hist[d]}")
    if out_of_month:
        print(f"  *** {out_of_month} counted entr(ies) have a booked_at OUTSIDE "
              f"{month_key}.")
        print("  *** The month reset did not clear them. That inflates the tally.")

    return {
        "counted": counted,
        "n": n,
        "gcal_keys": gcal_keys,
        "hint_keys": hint_keys,
        "file_month": file_month,
    }


# --------------------------------------------------------------------------
# PHASE 2 -- Google Calendar, read-only.
# --------------------------------------------------------------------------
def phase2(st, now, month_key, month_start):
    rule("PHASE 2 -- Google Calendar (read-only)")

    if st is None:
        print("  skipped (no state).")
        return None

    cal_id = os.getenv("GCAL_CALENDAR_ID")
    sa_key = os.getenv("GOOGLE_SA_KEY_FILE")
    if not cal_id or not sa_key:
        print("  n/a -- GCAL_CALENDAR_ID / GOOGLE_SA_KEY_FILE not set in this env.")
        print("  (Run from mbm-review-receiver so its .env loads.)")
        return None

    try:
        import gcal_bookings
    except Exception as e:
        print(f"  n/a -- could not import gcal_bookings: {e.__class__.__name__}: {e}")
        return None

    lookback = max(40, (now - month_start).days + 10)
    try:
        events = gcal_bookings.fetch_mbm_book_events(
            now, cal_id, sa_key, lookback, lookahead_days=400
        )
    except Exception as e:
        print(f"  n/a -- fetch raised {e.__class__.__name__}: {e}")
        return None

    if events is None:
        print("  n/a -- fetch_mbm_book_events returned None (hard auth/token failure).")
        print("  Do NOT read a ghost count off a failed fetch -- it would look like")
        print("  every booking was deleted.")
        return None

    print(f"  window: -{lookback}d / +400d around now, source-tagged mbm-book events")
    print(f"  live events returned: {len(events)}   (showDeleted=false, so this list")
    print("  is exactly 'not cancelled, not deleted')")

    live_ids = set()
    test_like = 0
    start_in_month = 0
    start_after_month = 0
    start_before_month = 0
    booked_this_month = 0
    for e in events:
        eid = e.get("id")
        if eid:
            live_ids.add(eid)
        # summary carries patient first names -- read, count, NEVER print.
        summ = str(e.get("summary") or "").lower()
        if "test" in summ:
            test_like += 1
        d = day_of(e.get("start"))
        if d:
            if d.startswith(month_key):
                start_in_month += 1
            elif d > month_key:
                start_after_month += 1
            else:
                start_before_month += 1
        priv = ((e.get("extendedProperties") or {}).get("private") or {})
        bd = day_of(priv.get("booked_at") or e.get("created"))
        if bd and bd.startswith(month_key):
            booked_this_month += 1

    print()
    print(f"  by appointment START:  in {month_key}: {start_in_month}"
          f"   after: {start_after_month}   before: {start_before_month}")
    print(f"  booked_at in {month_key}: {booked_this_month}"
          "   <- this is the slice the tally is entitled to")
    if test_like:
        print(f"  *** test-looking events still on the calendar: {test_like}")
        print("  *** (matched on the summary; not printed). Each one is +1 on the")
        print("  *** tally forever. Delete them and the tally still will not drop --")
        print("  *** see the ghost count below.")

    counted_gcal = {str(k)[5:] for k in st["gcal_keys"]}
    ghosts = counted_gcal - live_ids
    print()
    print(f"  counted gcal entries: {len(counted_gcal)}")
    print(f"  of those, still on the calendar: {len(counted_gcal) - len(ghosts)}")
    print(f"  *** GHOSTS (counted, no longer on the calendar): {len(ghosts)}")
    if ghosts:
        print("  Each ghost is a booking that was cancelled or deleted AFTER the")
        print("  tally counted it. showDeleted=false means the tally can never see")
        print("  it again, and there is no decay path, so it is permanent +1 each.")
        print("  A booking cancelled before it happened was never a consult.")
        print("  CAVEAT: an event whose START fell outside the fetch window above")
        print("  would also read as a ghost. Widen lookback/lookahead to rule that")
        print("  out before treating the number as final.")
    else:
        print("  No ghosts. The cancelled-booking over-count is not what is driving")
        print("  the gap right now (the mechanism still exists -- it just has not")
        print("  fired yet).")

    return {
        "live": len(events),
        "ghosts": len(ghosts),
        "test_like": test_like,
        "start_in_month": start_in_month,
        "start_after_month": start_after_month,
        "booked_this_month": booked_this_month,
    }


# --------------------------------------------------------------------------
# PHASE 3 -- Hint appointments, read-only. Reproduce the "8".
# --------------------------------------------------------------------------
def _hint_get(requests, base, path, params, key):
    """Bearer, matching export_dashboard_members.py:114. Basic kept as a fallback."""
    url = base + path
    r = requests.get(url, params=params,
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code in (401, 403):
        r2 = requests.get(url, params=params, auth=(key, ""), timeout=30)
        if r2.status_code < 400:
            return r2, "basic"
        return r, "bearer(%d) + basic(%d)" % (r.status_code, r2.status_code)
    return r, "bearer"


def phase3(st, now, month_key, month_start, month_end):
    rule("PHASE 3 -- Hint appointments (read-only) -- reproducing the \"8\"")

    key = os.getenv("HINT_API_KEY")
    if not key:
        print("  n/a -- HINT_API_KEY not set in this env.")
        return None
    try:
        import requests
    except Exception as e:
        print(f"  n/a -- requests unavailable: {e}")
        return None

    # Exact-match on "production", same as export_dashboard_members.py:84-85.
    # Deliberately NOT case-insensitive: being more lenient here than the exporter
    # could point this script at production while the dashboard reads sandbox.
    base = ("https://api.hint.com"
            if os.getenv("HINT_ENV") == "production"
            else "https://api.sandbox.hint.com")
    print(f"  base: {base}   (HINT_ENV={os.getenv('HINT_ENV') or 'unset'})")

    # The endpoint caps the range at 31 days; chunk to 28 to stay clear of it.
    appts = {}
    auth_style = "?"
    cur = month_start
    while cur < month_end:
        chunk_end = min(cur + timedelta(days=28), month_end)
        offset = 0
        while True:
            params = {
                "start_date": cur.strftime("%Y-%m-%d"),
                "end_date": chunk_end.strftime("%Y-%m-%d"),
                "limit": 100,
                "offset": offset,
            }
            try:
                r, auth_style = _hint_get(requests, base,
                                          "/api/provider/appointments", params, key)
            except Exception as e:
                print(f"  n/a -- request raised {e.__class__.__name__}: {e}")
                return None
            if r.status_code >= 400:
                print(f"  *** HTTP {r.status_code} (auth style tried: {auth_style}) "
                      f"for {params['start_date']}..{params['end_date']}")
                print("  Cannot reproduce the 8. Everything below is skipped.")
                return None
            try:
                body = r.json()
            except Exception:
                print("  *** response was not JSON. Skipping phase 3.")
                return None
            page = body if isinstance(body, list) else (
                body.get("appointments") or body.get("data") or body.get("results") or []
            )
            for a in page:
                if isinstance(a, dict) and a.get("id"):
                    appts[a["id"]] = a
            if len(page) < 100:
                break
            offset += 100
        cur = chunk_end

    print(f"  auth style that worked: {auth_style}")
    print(f"  appointments in {month_key}: {len(appts)}")
    if not appts:
        return None

    field_names = set()
    for a in appts.values():
        field_names |= set(a.keys())
    print(f"  appointment field NAMES present: {sorted(field_names)}")

    start_field = next((f for f in START_FIELD_CANDIDATES if f in field_names), None)
    created_field = next((f for f in CREATED_FIELD_CANDIDATES if f in field_names), None)
    status_field = next((f for f in STATUS_FIELD_CANDIDATES if f in field_names), None)
    print(f"  using start={start_field!r} created={created_field!r} "
          f"status={status_field!r}")

    statuses = Counter(str(a.get(status_field) or "(none)").lower()
                       for a in appts.values()) if status_field else Counter()
    if statuses:
        print(f"  status values seen: {dict(statuses)}")

    try:
        from consult_count import is_consult_booking
    except Exception:
        def is_consult_booking(a):
            for att in (a.get("attendees") or []):
                if (att.get("type") or "").lower() == "staff":
                    continue
                return ((att.get("patient") or {}) or {}).get("id") is None
            return False
        print("  (using a local copy of is_consult_booking -- import failed)")

    consults = [a for a in appts.values() if is_consult_booking(a)]
    print(f"  of those, Contact-attendee consults: {len(consults)}")

    def in_month(a, field):
        d = day_of(a.get(field)) if field else None
        return bool(d and d.startswith(month_key))

    today = now.strftime("%Y-%m-%d")
    cand = {}
    cand["A  non-cancelled consults, START in month  (the documented 8)"] = sum(
        1 for a in consults if in_month(a, start_field) and not is_dead(a, status_field))
    cand["B  ALL consults incl. cancelled, START in month"] = sum(
        1 for a in consults if in_month(a, start_field))
    cand["C  non-cancelled consults, START in month and already past"] = sum(
        1 for a in consults if in_month(a, start_field)
        and not is_dead(a, status_field)
        and (day_of(a.get(start_field)) or "9999") <= today)
    cand["D  non-cancelled consults, CREATED in month"] = sum(
        1 for a in consults if in_month(a, created_field)
        and not is_dead(a, status_field)) if created_field else None
    cand["E  ALL appointments (not just consults), non-cancelled, START in month"] = sum(
        1 for a in appts.values() if in_month(a, start_field)
        and not is_dead(a, status_field))

    print()
    print("  candidate definitions of the dashboard's scheduled_mtd:")
    for k, v in cand.items():
        print(f"    {v if v is not None else 'n/a':>4}   {k}")
    print("  Whichever of these equals the number on the dashboard is the real")
    print("  definition. If none match, the exporter is doing something else and")
    print("  export_dashboard_members.py line ~1004 is the place to look.")

    hint_ids = set(st["hint_keys"]) if st else set()
    present = len(hint_ids & set(appts.keys()))
    print()
    print(f"  counted hint-keyed tally entries: {len(hint_ids)}")
    print(f"  of those, still visible in this month's Hint window: {present}")
    print(f"  no longer visible: {len(hint_ids) - present}")
    print("  'No longer visible' is mostly the intended case: Hint erases the")
    print("  Contact attendee when a consult converts to a patient, so the")
    print("  appointment stops looking like a consult. That is exactly why the")
    print("  tally must not decay -- a converted consult still happened.")

    return {"consults": len(consults), "cand": cand,
            "hint_counted": len(hint_ids), "hint_visible": present}


# --------------------------------------------------------------------------
# PHASE 4 -- reconciliation + verdict.
# --------------------------------------------------------------------------
def phase4(st, g, h, month_key):
    rule("PHASE 4 -- reconciliation")

    if st is None:
        print("  nothing to reconcile.")
        return

    print(f"  running tally (booked_mtd_running_tally) = {st['n']}")
    print(f"      = {len(st['hint_keys'])} hint-keyed + {len(st['gcal_keys'])} gcal-keyed")
    print()
    print("  Of the gcal half, scheduled_mtd can see ZERO of it -- not some of it,")
    print("  zero. Hint's appointments API has no record of an mbm-book booking.")
    print("  Since the 2026-07-18 cutover that is where essentially all web")
    print("  bookings live, so the gap is mostly this one fact.")

    if g:
        print()
        print("  gcal half decomposes as:")
        print(f"    live on the calendar, booked in {month_key}: {g['booked_this_month']}")
        print(f"    live but START is next month (real, just not yet due):"
              f" {g['start_after_month']}")
        print(f"    ghosts -- cancelled/deleted, permanently stuck in the tally:"
              f" {g['ghosts']}")
        print(f"    test-looking events still live: {g['test_like']}")
        overcount = g["ghosts"] + g["test_like"]
        if overcount:
            print()
            print(f"  *** OVER-COUNT: at least {overcount} of the {st['n']} were never")
            print("  *** real consults. That is the defect, and it is not self-healing.")
        else:
            print()
            print("  No measurable over-count today.")

    if h:
        a = h["cand"].get(
            "A  non-cancelled consults, START in month  (the documented 8)")
        print()
        print(f"  Hint side: {a} non-cancelled Contact-attendee consults with a start")
        print(f"  date in {month_key}. If that matches the dashboard, scheduled_mtd is")
        print("  working exactly as written -- it is just answering a different")
        print("  question than the tally.")

    rule("VERDICT")
    print("  These are two different metrics, not one metric and one bug:")
    print("    tally         = 'how many consults did we BOOK this month, from")
    print("                     everywhere, cumulative, never revised'")
    print("    scheduled_mtd = 'how many Hint consults are ON THE CALENDAR for this")
    print("                     month right now, cancellations removed'")
    print("  Expecting them to match was the error. They should be labelled so that")
    print("  nobody expects it again.")
    print()
    print("  There IS one real defect: the tally has no way to un-count a booking")
    print("  that was cancelled or deleted after it was counted, because")
    print("  fetch_mbm_book_events passes showDeleted=\"false\" and the cancellation")
    print("  watcher works by disappearance. Ghosts and test bookings accumulate")
    print("  forever.")
    print()
    print("  RECOMMENDED FIX (not applied -- this script is read-only):")
    print("   1. Snapshot enough per gcal entry to re-check it: store")
    print("      {booked_at, start} instead of a bare date string. The cancellation")
    print("      watcher already snapshots start/phone for exactly this reason.")
    print("   2. On each tally pass, for gcal entries whose START is still in the")
    print("      future, drop any that are absent from a SUCCESSFUL fetch. Future")
    print("      only -- a past event may age out of the window, and a consult that")
    print("      already happened must never be un-counted.")
    print("   3. Never act on a fetch that returned None. That is already the")
    print("      convention elsewhere; keep it.")
    print("   4. Filter test bookings at count time on the same rule the calendar")
    print("      cleanup uses, so deleting them later is not required.")
    print()
    print("  Cheapest immediate win: delete the stale test events, then apply (1)+(2)")
    print("  so the ghosts they leave behind actually clear.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="phase 1 only; no Google or Hint calls")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass

    now = datetime.now().astimezone()  # tz-aware: a naive timeMin gets HTTP 400 from Google
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (month_start + timedelta(days=32)).replace(day=1)
    month_key = month_start.strftime("%Y-%m")

    print(f"consult tally diagnostic -- {now:%Y-%m-%d %H:%M} local, month {month_key}")
    print("READ-ONLY. Output is aggregate-only and safe to paste.")

    st = phase1(month_key)

    if args.offline:
        rule("PHASE 2 / 3 -- skipped (--offline)")
        g = h = None
    else:
        g = phase2(st, now, month_key, month_start)
        h = phase3(st, now, month_key, month_start, nxt)

    phase4(st, g, h, month_key)
    return 0 if st is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
