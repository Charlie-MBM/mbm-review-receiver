#!/usr/bin/env python3
"""
Count NEW-PATIENT CONSULTS from James's Google Calendar - the real source of truth
since the 2026-07-18 MBM Book cutover.

WHY THIS EXISTS
---------------
consult_count.py identifies a consult as "an appointment whose non-staff attendee is
still a Contact (patient.id is None)". That rule died at the cutover:
  * MBM Book does an INSTANT Hint patient-create at booking, so a patient id exists
    from the moment of booking; and
  * Hint now only receives a GENERIC BUSY BLOCK - it carries no consult identity at all.
Evidence (consult_count_state.json, read 2026-07-31): 10 consults counted Jul 1-17,
only 2 counted Jul 18-31, while the calendar shows ~6 in the week of Jul 26 alone.
The Hint-side metric is unrecoverable going forward. The calendar is authoritative.

WHAT IT EMITS - AGGREGATE INTEGERS ONLY
---------------------------------------
No names, no phones, no emails, no event titles. The only strings that ever leave this
script are consult TYPE tokens parsed from the parenthetical in the title
("Free Consult (Ketamine)" -> "ketamine"), and each one is validated against a strict
whitelist pattern before it is allowed into the output. Run it on the laptop; the
output is safe to paste anywhere the rest of members_feed.json goes.

  booked_mtd  - consults BOOKED this month (booked_at basis; replaces the dead tally)
  held_mtd    - consults that have already STARTED this month  <- the number that was
                never measurable from Hint
  upcoming    - future-dated consults from now forward (default +60d)

SELF-SERVE vs PHONE (the honest split)
--------------------------------------
Charlie and James book call-in consults through the STAFF-ONLY page
(/book-beta?staff=1) - a different page from the customer-facing one. Those bookings
still flow through MBM Book, so "has the mbm-book tag" does NOT mean "booked online".
The Worker writes the real discriminator into extendedProperties.private:
    channel = "web"          -> customer booked it themselves online
    channel = "staff-phone"  -> Charlie/James booked it for a caller (staff page)
    (absent)                 -> legacy/manual: pre-cutover, or typed onto the calendar
That split is reported directly, so self-serve conversion is never inflated by the
consults the office booked by hand.

USAGE
  py consult_count_gcal.py                 # human-readable summary
  py consult_count_gcal.py --json          # aggregate JSON block (feed-ready)
  py consult_count_gcal.py --month 2026-07 # a specific month (default: current)
  py consult_count_gcal.py --lookahead 90
"""
import os
import re
import sys
import json
import argparse
import datetime as dt
from pathlib import Path
from collections import Counter

try:
    from zoneinfo import ZoneInfo
except ImportError:
    print("Python 3.9+ required (zoneinfo).", file=sys.stderr)
    raise

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

import requests as http

# Reuse the existing service-account token helper - same read-only calendar scope,
# same key file. No new credentials, no new auth path.
from gcal_bookings import _access_token, GCAL_SOURCE_TAG

# ---------------------------------------------------------------------------
# PAID ATTRIBUTION - import the REAL, already-tested functions rather than
# writing a second copy that can drift (_test_gads_logic.py pins their behaviour).
# ---------------------------------------------------------------------------
_POLLER = (Path(__file__).resolve().parent.parent
           / "mbm-hint-enrollment" / "webhook" / "send_consult_intake.py")
_has_paid_google_signal = None
ATTRIB_SOURCE = "imported"
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_sci", _POLLER)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _has_paid_google_signal = _mod._has_paid_google_signal
    # resolve_gcal_lead_source() -> (source, self_reported_bool). Prefers the lead-source
    # CHIP the front desk taps when booking a caller on the staff page, and falls back to
    # deriving from click ids / referrer. This is how PHONE bookings get attributed at
    # all: their click ids are stripped upstream, so metadata alone yields nothing.
    resolve_lead_source = _mod.resolve_gcal_lead_source
except Exception:
    def resolve_lead_source(priv):              # degraded fallback, reported below
        return (None, False)
    ATTRIB_SOURCE = "local-mirror"
    PAID_MEDIUMS = ("cpc", "ppc", "paid", "paidsearch", "paid_search", "ads")

    def _has_paid_google_signal(priv):            # mirror of the poller's version
        if not isinstance(priv, dict):
            return False
        for k in ("gclid", "gbraid", "wbraid"):
            v = priv.get(k)
            if (v.strip() if isinstance(v, str) else v):
                return True
        med = priv.get("utm_medium")
        med = med.strip().lower() if isinstance(med, str) else ""
        if med in PAID_MEDIUMS:
            return True
        src = priv.get("utm_source")
        src = src.strip().lower() if isinstance(src, str) else ""
        return src in ("google_ads", "googleads", "adwords")

TZ = ZoneInfo("America/Los_Angeles")
CAL_ID = os.environ.get("GCAL_CALENDAR_ID", "")
SA_KEY = os.environ.get("GOOGLE_SA_KEY_FILE", "")

# A new-patient consult block. Matches "Free Consult — Lou", "Free consult (Ketamine) — E".
# Only the optional parenthetical is ever captured; the name after the dash is never read.
TITLE_RE = re.compile(r"^\s*free\s*consult\s*(?:\(\s*([^)]{0,40})\s*\))?", re.I)
# Hard PHI guard: a type token may only be simple words. Anything else -> "other".
SAFE_TYPE_RE = re.compile(r"^[A-Za-z0-9 /&+\-]{1,40}$")


def _month_bounds(month_str):
    if month_str:
        y, m = (int(x) for x in month_str.split("-"))
    else:
        now = dt.datetime.now(TZ)
        y, m = now.year, now.month
    start = dt.datetime(y, m, 1, tzinfo=TZ)
    end = dt.datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=TZ)
    return start, end


def fetch_events(time_min, time_max, token):
    """events.list over [time_min, time_max). Returns raw items (NOT logged/printed)."""
    import urllib.parse
    cal = urllib.parse.quote(CAL_ID, safe="")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
    out, page_token = [], None
    while True:
        params = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",
            "showDeleted": "false",
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if page_token:
            params["pageToken"] = page_token
        r = http.get(url, headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("items", []) or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return out


def consult_type(summary):
    m = TITLE_RE.match(summary or "")
    if not m:
        return None
    raw = (m.group(1) or "general").strip().lower()
    return raw if SAFE_TYPE_RE.match(raw) else "other"


def is_consult(ev):
    """True if this event is a new-patient consult block."""
    if ev.get("status") == "cancelled":
        return False
    return TITLE_RE.match(ev.get("summary") or "") is not None


def _priv(ev):
    return ((ev.get("extendedProperties") or {}).get("private") or {})


def booking_channel(ev):
    """How the consult got booked, from the Worker's own tag.

    "web"         - the customer booked it themselves on the public page
    "staff-phone" - Charlie/James booked it for a caller via /book-beta?staff=1
                    (a DIFFERENT page from the customer-facing one)
    "legacy-or-manual" - no channel tag: pre-cutover, or typed onto the calendar

    NB: a staff-phone booking still carries source=mbm-book, so the mbm-book tag
    alone can NOT be used to mean "booked online" - that would count every call-in
    consult as self-serve and inflate the booking page's conversion rate.
    """
    p = _priv(ev)
    ch = (p.get("channel") or "").strip().lower()
    if ch in ("web", "staff-phone"):
        return ch
    if ch:
        return "other"
    return "legacy-or-manual"


def _start(ev):
    s = (ev.get("start") or {})
    v = s.get("dateTime") or s.get("date")
    if not v:
        return None
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:                       # all-day event
        d = d.replace(tzinfo=TZ)
    return d.astimezone(TZ)


def _booked_at(ev):
    v = _priv(ev).get("booked_at") or ev.get("created")
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM (default: current month)")
    ap.add_argument("--lookahead", type=int, default=60, help="days forward for 'upcoming'")
    ap.add_argument("--json", action="store_true", help="emit the aggregate JSON block")
    args = ap.parse_args()

    if not CAL_ID or not SA_KEY:
        print("GCAL_CALENDAR_ID / GOOGLE_SA_KEY_FILE not set in .env", file=sys.stderr)
        sys.exit(2)
    token = _access_token(SA_KEY)
    if not token:
        print("could not mint a Google access token (check GOOGLE_SA_KEY_FILE)", file=sys.stderr)
        sys.exit(2)

    now = dt.datetime.now(TZ)
    m_start, m_end = _month_bounds(args.month)
    # One window wide enough for: this month + everything upcoming. Bookings made this
    # month for a future date need the forward window too.
    lo = min(m_start, now) - dt.timedelta(days=1)
    hi = max(m_end, now + dt.timedelta(days=args.lookahead))
    events = [e for e in fetch_events(lo, hi, token) if is_consult(e)]

    booked = held = upcoming = 0
    paid_booked = 0
    paid_by_day = Counter()
    booked_by_source = Counter()   # resolved lead source -> the channel-econ rows
    selfrep_by_source = Counter()  # of those, how many are a real self-report (chip)
    phone_by_source = Counter()    # staff-phone bookings only, by source
    chan_booked = Counter()        # web / staff-phone / legacy-or-manual
    chan_held = Counter()
    types_held = Counter()
    types_upcoming = Counter()
    booked_by_day = Counter()

    seen = set()
    for ev in events:
        eid = ev.get("id")
        if eid in seen:
            continue
        seen.add(eid)

        st = _start(ev)
        ba = _booked_at(ev)
        typ = consult_type(ev.get("summary")) or "general"
        chan = booking_channel(ev)

        if ba and m_start <= ba < m_end:
            booked += 1
            booked_by_day[ba.date().isoformat()] += 1
            chan_booked[chan] += 1
            # PAID denominator: only consults carrying hard evidence of a paid Google
            # click. Staff-phone bookings can never qualify - the Worker strips click
            # ids on that path (office browser != the caller), which is correct.
            if _has_paid_google_signal(_priv(ev)):
                paid_booked += 1
                paid_by_day[ba.date().isoformat()] += 1
            # Per-channel attribution for the channel-economics table.
            # Self-report (the staff/web lead-source chip) wins; metadata is the
            # fallback. None = no signal at all -> "unattributed", never guessed.
            src, self_rep = resolve_lead_source(_priv(ev))
            src = src or "unattributed"
            booked_by_source[src] += 1
            if self_rep:
                selfrep_by_source[src] += 1
            if chan == "staff-phone":
                phone_by_source[src] += 1

        if st and m_start <= st < m_end and st <= now:
            held += 1
            types_held[typ] += 1
            chan_held[chan] += 1

        if st and now < st <= now + dt.timedelta(days=args.lookahead):
            upcoming += 1
            types_upcoming[typ] += 1

    block = {
        "month": m_start.strftime("%Y-%m"),
        "source": "google-calendar",
        "basis": ("consult = calendar event whose title starts 'Free Consult'; "
                  "counts only, no identifiers exported"),
        "booked_mtd": booked,
        "held_mtd": held,
        "upcoming": upcoming,
        "upcoming_window_days": args.lookahead,
        # channel = the Worker's own tag. staff-phone means Charlie/James booked it
        # for a caller on /book-beta?staff=1 - NOT a self-serve online booking.
        "booked_self_serve_web": chan_booked.get("web", 0),
        "booked_staff_phone": chan_booked.get("staff-phone", 0),
        "booked_legacy_or_manual": chan_booked.get("legacy-or-manual", 0),
        # --- ad-efficiency inputs (these feed the CHANNEL ECONOMICS table, not the
        # North Star tile). paid_google_booked is the ONLY honest denominator for a
        # Google Ads cost-per-consult: dividing ad spend by consults from every
        # source understates true paid CPA, sometimes by several times.
        "paid_google_booked": paid_booked,
        "paid_google_by_day": dict(sorted(paid_by_day.items())),
        "booked_by_source": dict(booked_by_source),
        "selfreported_by_source": dict(selfrep_by_source),
        # Phone bookings, by what the caller told the front desk (the chip). This is
        # the ONLY attribution a phone booking can carry: the staff page strips click
        # ids by design, so metadata yields nothing for these.
        "phone_booked_by_source": dict(phone_by_source),
        "attrib_impl": ATTRIB_SOURCE,   # "imported" = real poller fns; "local-mirror" = fallback
        "held_by_channel": dict(chan_held),
        "held_by_type": dict(types_held),
        "upcoming_by_type": dict(types_upcoming),
        "booked_by_day": dict(sorted(booked_by_day.items())),
        "generated_at": now.astimezone(dt.timezone.utc).isoformat(),
    }

    if args.json:
        print(json.dumps({"consults_gcal": block}, indent=2))
        return

    print(f"\n  NEW-PATIENT CONSULTS - {block['month']}  (Google Calendar, authoritative)")
    print(f"  {'-'*58}")
    print(f"  booked this month : {booked:>4}")
    print(f"      self-serve web : {chan_booked.get('web', 0):>4}   (customer booked it themselves)")
    print(f"      staff phone    : {chan_booked.get('staff-phone', 0):>4}   (you/James on ?staff=1)")
    if chan_booked.get("legacy-or-manual"):
        print(f"      legacy/manual  : {chan_booked['legacy-or-manual']:>4}   (untagged - pre-cutover or hand-typed)")
    if chan_booked.get("other"):
        print(f"      other/unknown  : {chan_booked['other']:>4}")
    print(f"  held so far       : {held:>4}   <- never measurable from Hint")
    print(f"  upcoming (+{args.lookahead}d)   : {upcoming:>4}")
    print(f"\n  AD EFFICIENCY (feeds the channel-economics table)")
    print(f"  {'-'*58}")
    print(f"  paid-Google consults : {paid_booked:>4}   of {booked} booked"
          f"  ({'no paid signal' if not paid_booked else f'{paid_booked/booked*100:.0f}% of bookings'})")
    if booked_by_source:
        print(f"  by source            : " + ", ".join(
            f"{k} {v}" + (f" ({selfrep_by_source[k]} self-rep)" if selfrep_by_source.get(k) else "")
            for k, v in booked_by_source.most_common()))
    if phone_by_source:
        print(f"  phone bookings       : " + ", ".join(f"{k} {v}" for k, v in phone_by_source.most_common()))
        print(f"     ^ from the lead-source chip the front desk taps. Click ids are")
        print(f"       stripped on staff bookings, so the chip is their ONLY attribution.")
        print(f"       NB a chip saying 'Google' can NOT be refined to 'Google Ads' on a")
        print(f"       phone booking - so ad-driven CALLS stay uncredited to Ads. Google")
        print(f"       Ads call reporting is the only ground truth for those.")
    if ATTRIB_SOURCE != "imported":
        print(f"  !! attribution running on the LOCAL MIRROR (could not import the poller's")
        print(f"     derive_lead_source) - by-source counts are degraded; paid signal is fine.")
    print(f"\n  NB: divide ad spend by paid-Google consults, never by all {booked}.")
    print(f"  Click ids get lost (cross-device, iOS, stripped on staff bookings), so the")
    print(f"  paid count is a FLOOR - true paid CPA is <= whatever you compute from it.")

    if types_held:
        print(f"\n  held by type     : " + ", ".join(f"{k} {v}" for k, v in types_held.most_common()))
    if types_upcoming:
        print(f"  upcoming by type : " + ", ".join(f"{k} {v}" for k, v in types_upcoming.most_common()))
    if booked_by_day:
        print(f"\n  booked by day:")
        for d, n in sorted(booked_by_day.items()):
            print(f"    {d}  {'#'*n} {n}")
    print(f"\n  Compare: Hint running tally read 25 booked / 8 'completed' for 2026-07,")
    print(f"  which stopped working at the 2026-07-18 cutover. See DASHBOARD_METRICS.md.\n")


if __name__ == "__main__":
    main()
