#!/usr/bin/env python3
"""
send_daily_summary.py - Daily ops digest for Mt. Baker Medical.
================================================================
Sends Charlie + James a once-a-day, COUNTS-ONLY summary, in funnel order:

  * Phone clicks    - GA4 phone_click events (website tel: taps; top of funnel)
  * Booking clicks  - GA4 booking_click events (website Book Now taps)
  * Initial consults- free consults booked in Hint (Contact-attendee; any channel)
  * New members     - memberships newly enrolled AND paid
  * Pending enroll  - memberships created but not yet billed/confirmed (Auto Confirm
                      is OFF, so these are awaiting staff confirmation + payment)
  * Review reqs sent- Spruce review-request SMSes (--window last_7d only)
  * (deferred) Reviews received - new Google reviews via GBP API, once approved

Each line: "<label>: <period> (<MTD> MTD)". Review requests use "total" instead
of MTD (cumulative all-time). Daily digest fires Mon-Sat 10am via Task Scheduler;
weekly digest is intended to run Monday 10am via --window last_7d, replacing the
daily cadence (Charlie reconfigures the schedule).

Primary channel is SMS via Spruce (already configured + BAA-covered, same line the
review poller uses). Email via Resend is an optional secondary channel.

WHY THIS IS SAFE (no PHI)
-------------------------
The message is ONLY integer counts -- no names, no patient IDs, no contact info,
nothing that identifies a person or that they used a health service. It is not
PHI / not WA-MHMD consumer health data. Do NOT add patient-identifying detail to
this digest. The GA4 pull asks only for aggregate eventCount keyed by eventName
+ dateRange; no user-level dimensions are requested.

DATA SOURCES
------------
Hint Partner API (practices Bearer key, same key the review poller uses):
  GET /api/provider/appointments  (start_date/end_date window, paginated)
      -> id, start, end, status, created_at, attendees, ...
      Free-consult counting is delegated to consult_count.tally() which filters
      to appointments whose attendee is a Contact (no patient.id) -- Hint erases
      the Contact attendee on enrollment, so the tally persists ids in
      consult_count_state.json to avoid undercount-after-conversion.
  GET /api/provider/memberships   (created_at[gte] filter, paginated)
      -> id, created_at, status, enrollment_status, never_been_billed,
         last_bill_amount_in_cents, ...
      "New member" = created in window AND never_been_billed == False (a charge has
      run = payment collected) AND status not cancelled.
      "Pending"    = created in window, not cancelled, never billed yet.

GA4 Data API (property 513547844, service-account credentials):
  RunReport: dimensions=[eventName, dateRange], metric=eventCount,
             filter: eventName IN (phone_click, booking_click),
             date_ranges: [yesterday/today, month-to-date].
  If GA4 is not configured (no GA4_PROPERTY_ID or lib not installed) the two
  click lines are silently skipped and the digest still ships with Consults/
  Members/Pending. Transient GA4 errors degrade the same way.

RUN
---
  py send_daily_summary.py --dry-run     # print the digest, send nothing
  py send_daily_summary.py --send        # force a real send (ignores DRY_RUN env)
  py send_daily_summary.py               # honors DRY_RUN env (.env)
  py send_daily_summary.py --window 24h  # daily number = rolling last 24h
  py send_daily_summary.py --window last_7d  # weekly digest (Mon-Sun previous week)
  py send_daily_summary.py --selftest    # offline unit-check of the logic

ENV (reuses mbm-review-receiver/.env)
-------------------------------------
  HINT_ENV                 sandbox | production
  HINT_API_KEY             Hint practices API key
  SPRUCE_API_KEY           Spruce API key (for SMS)
  SPRUCE_INTERNAL_ENDPOINT_ID  Spruce phone line id (the (360) 295-9241 line)
  SUMMARY_SMS_TO           comma-separated recipient cells, e.g.
                           "+13603498094,+13601234567"  (Charlie, James)
  SUMMARY_EMAIL_TO         optional; if set AND RESEND_API_KEY set, also emails
  RESEND_API_KEY           optional Resend key for the email channel
  FROM_EMAIL               default "Mt. Baker Medical <care@mtbakermedical.com>"
  SUMMARY_HORIZON_DAYS     forward appointment-sweep horizon (default 60)
  GA4_PROPERTY_ID          GA4 property id, e.g. "513547844". Unset = skip clicks.
  GOOGLE_APPLICATION_CREDENTIALS  path to GCP service-account JSON for GA4 read.
  DRY_RUN                  true|false
"""

import argparse
import pathlib
import json
import os
import sys
import consult_count
from datetime import datetime, timedelta, timezone

# Friends & Family / comp memberships are real signups but NOT acquisition new members.
# Reuse export_dashboard_members' rule rather than restating it here -- these two counts
# drifting apart is exactly what produced "14 new members" in the digest vs 3 in the
# dashboard (the 11 F&F comps).
try:
    from export_dashboard_members import is_friends_family, plan_name_of
except Exception:  # pragma: no cover - keep the digest shipping if the import breaks
    def plan_name_of(m):
        return ((m.get("plan") or {}).get("name")) or ""

    def is_friends_family(plan_name):
        n = (plan_name or "").lower()
        return any(s in n for s in ("friends and family", "friends & family", "friend", "f&f"))

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover
    PACIFIC = timezone(timedelta(hours=-7))

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except Exception:
    pass

try:
    import requests as http
except ImportError:  # pragma: no cover
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

HINT_ENV = os.environ.get("HINT_ENV", "sandbox")
HINT_BASE_URL = "https://api.hint.com" if HINT_ENV == "production" else "https://api.sandbox.hint.com"
HINT_API_KEY = os.environ.get("HINT_API_KEY", "")

SPRUCE_API_KEY = os.environ.get("SPRUCE_API_KEY", "")
SPRUCE_INTERNAL_ENDPOINT_ID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
SPRUCE_BASE_URL = "https://api.sprucehealth.com/v1"
SUMMARY_SMS_TO = os.environ.get("SUMMARY_SMS_TO", "")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Mt. Baker Medical <care@mtbakermedical.com>")
SUMMARY_EMAIL_TO = os.environ.get("SUMMARY_EMAIL_TO", "")

HORIZON_DAYS = int(os.environ.get("SUMMARY_HORIZON_DAYS", "60"))
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
# GOOGLE_APPLICATION_CREDENTIALS is read implicitly by google.analytics.data_v1beta.


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}")


# ---------------------------------------------------------------------------
# Time helpers (boundaries in Pacific, tz-aware)
# ---------------------------------------------------------------------------
def day_bounds_pacific(now=None, window="yesterday"):
    now = now or datetime.now(PACIFIC)
    now = now.astimezone(PACIFIC)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today0.replace(day=1)
    if window == "today":
        return today0, now, month_start, f"Today ({today0.strftime('%a %b')} {today0.day})"
    if window == "24h":
        return now - timedelta(hours=24), now, month_start, "Last 24 hours"
    if window == "last_7d":
        # Rolling 7 days ending at midnight last night. When run Monday 10am
        # this covers Mon 00:00 of prior week through Mon 00:00 of this week,
        # i.e. the seven full days Mon-Sun that just finished.
        end = today0
        start = today0 - timedelta(days=7)
        last = end - timedelta(days=1)
        label = f"Week of {start.strftime('%b')} {start.day} - {last.strftime('%b')} {last.day}"
        return start, end, month_start, label
    y0 = today0 - timedelta(days=1)
    return y0, today0, month_start, f"Yesterday ({y0.strftime('%a %b')} {y0.day})"


def parse_dt(s):
    if not s:
        return None
    try:
        s = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            return datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            return None


def in_window(dt, start, end):
    return dt is not None and start <= dt < end


def normalize_e164(phone):
    """Normalize free-form phone to E.164 (US default). Ported from the receiver."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


# ---------------------------------------------------------------------------
# Hint API
# ---------------------------------------------------------------------------
def _headers():
    return {"Authorization": f"Bearer {HINT_API_KEY}", "Content-Type": "application/json"}


def _get(url, params):
    resp = http.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("data", []) or []
    return data


def fetch_appointments(month_start_date, horizon_date):
    url = f"{HINT_BASE_URL}/api/provider/appointments"
    seen, out = set(), []
    win_start = month_start_date
    while win_start <= horizon_date:
        win_end = min(win_start + timedelta(days=30), horizon_date)
        offset, limit = 0, 100
        while True:
            try:
                batch = _get(url, {
                    "start_date": win_start.date().isoformat(),
                    "end_date": win_end.date().isoformat(),
                    "limit": limit, "offset": offset,
                })
            except Exception as e:
                log(f"error: appointments {win_start.date()}..{win_end.date()} offset={offset}: {e}")
                break
            for a in batch:
                aid = a.get("id")
                if aid and aid not in seen:
                    seen.add(aid)
                    out.append(a)
            if len(batch) < limit:
                break
            offset += limit
        win_start = win_end + timedelta(days=1)
    return out


def fetch_memberships(since_dt):
    url = f"{HINT_BASE_URL}/api/provider/memberships"
    out, offset, limit = [], 0, 100
    since_iso = since_dt.astimezone(timezone.utc).isoformat()
    while True:
        try:
            batch = _get(url, {"created_at[gte]": since_iso, "limit": limit, "offset": offset})
        except Exception as e:
            log(f"error: memberships offset={offset}: {e}")
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return out


# ---------------------------------------------------------------------------
# GA4 Data API (aggregate event counts only; no user-level dimensions)
# ---------------------------------------------------------------------------
def count_ga4_events(event_names, daily_start, daily_end, month_start):
    """Pull daily + MTD event counts from GA4 for the given event names.

    Returns {event_name: (daily_count, mtd_count)} for every requested event,
    or {} if GA4 is not configured / the library is missing / the call fails.
    The caller renders the lines only for events present in the returned dict,
    so an outage degrades to the original 3-line digest with no breakage.

    Privacy posture: dimensions are eventName + dateRange only; metric is
    eventCount; filter is an eventName allowlist. No user, session, page, or
    location dimensions are requested. The response shape contains nothing
    that identifies a website visitor.
    """
    if not GA4_PROPERTY_ID:
        return {}
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, Filter, FilterExpression, RunReportRequest,
        )
    except ImportError:
        log("warning: google-analytics-data not installed; GA4 click lines skipped.")
        return {}

    # GA4 date ranges are inclusive on both ends. daily_end / month-end are
    # exclusive midnights in this script; step back 1us to get the last full
    # day to include in the inclusive range.
    d_last = (daily_end - timedelta(microseconds=1)).date()
    m_last = d_last  # MTD shares the same upper bound as the daily window.
    d_start = daily_start.date()
    m_start = month_start.date()

    try:
        client = BetaAnalyticsDataClient()
        req = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            dimensions=[Dimension(name="eventName"), Dimension(name="dateRange")],
            metrics=[Metric(name="eventCount")],
            date_ranges=[
                DateRange(start_date=d_start.isoformat(),
                          end_date=d_last.isoformat(), name="daily"),
                DateRange(start_date=m_start.isoformat(),
                          end_date=m_last.isoformat(), name="mtd"),
            ],
            dimension_filter=FilterExpression(filter=Filter(
                field_name="eventName",
                in_list_filter=Filter.InListFilter(values=list(event_names)),
            )),
            limit=100,
        )
        resp = client.run_report(req)
    except Exception as e:
        log(f"warning: GA4 fetch failed; click lines skipped: {e}")
        return {}

    buckets = {name: [0, 0] for name in event_names}  # [daily, mtd]
    for row in resp.rows:
        event = row.dimension_values[0].value
        range_name = row.dimension_values[1].value
        try:
            count = int(row.metric_values[0].value or 0)
        except (TypeError, ValueError):
            count = 0
        if event not in buckets:
            continue
        if range_name == "daily":
            buckets[event][0] = count
        elif range_name == "mtd":
            buckets[event][1] = count
    return {k: (v[0], v[1]) for k, v in buckets.items()}



# ---------------------------------------------------------------------------
# Review-requests-sent tally (reads patient_state.json, emits counts only)
# ---------------------------------------------------------------------------
REVIEW_STATE_FILE = pathlib.Path(__file__).parent / "patient_state.json"


def count_review_requests(week_start, week_end):
    """Return (week_count, all_time_total) of review-request SMSes sent.

    Source: patient_state.json (written by send_review_requests.py). Schema:
        {"<patient_id>": {"count": <int>, "last_ask_ts": "<ISO 8601 UTC>"},
         "_poller_meta": {...}}

    all_time_total = sum of "count" across all patient entries.
    week_count     = patients whose last_ask_ts falls in [week_start, week_end).
                     The poller's 30-day cooldown per patient means each
                     patient appears at most once in any 7-day window, so
                     counting by last_ask_ts is correct (no double-counting).

    Privacy: this function reads patient IDs internally but returns ONLY
    aggregate integers. The IDs are never returned, printed, or logged.
    Counts are not PHI.
    """
    try:
        state = json.loads(REVIEW_STATE_FILE.read_text())
    except FileNotFoundError:
        log("info: patient_state.json missing; review-requests-sent = 0.")
        return 0, 0
    except Exception as e:
        log(f"warning: could not read patient_state.json: {e}")
        return 0, 0

    week_count = 0
    total = 0
    for pid, p in state.items():
        if pid.startswith("_"):  # skip _poller_meta etc.
            continue
        if not isinstance(p, dict):
            continue
        try:
            total += int(p.get("count") or 0)
        except (TypeError, ValueError):
            pass
        ts = parse_dt(p.get("last_ask_ts"))
        if ts is not None and in_window(ts, week_start, week_end):
            week_count += 1
    return week_count, total


# ---------------------------------------------------------------------------
# Counting (pure - covered by --selftest)
# ---------------------------------------------------------------------------
DEAD_APPT = {"cancelled", "declined"}
DEAD_MEMBERSHIP = {"cancelled", "canceled", "terminated", "void", "voided"}


def count_bookings(appts, daily_start, daily_end, month_start):
    day = mtd = 0
    for a in appts:
        if (a.get("status") or "").lower() in DEAD_APPT:
            continue
        created = parse_dt(a.get("created_at"))
        if created is None:
            continue
        if in_window(created, month_start, daily_end):
            mtd += 1
        if in_window(created, daily_start, daily_end):
            day += 1
    return day, mtd


def _membership_is_paid(m):
    nbb = m.get("never_been_billed")
    if isinstance(nbb, bool):
        return nbb is False
    last_amt = m.get("last_bill_amount_in_cents")
    return isinstance(last_amt, int) and last_amt > 0


def count_members(memberships, daily_start, daily_end, month_start):
    paid_day = paid_mtd = pend_day = pend_mtd = ff_mtd = 0
    for m in memberships:
        status = (m.get("status") or m.get("enrollment_status") or "").lower()
        if status in DEAD_MEMBERSHIP:
            continue
        # Comp / Friends & Family: a real membership, but not an acquired new member.
        # Without this the digest counted them and reported 14 MTD against the
        # dashboard's 3 (11 F&F comps). Counted separately, never in paid/pending.
        if is_friends_family(plan_name_of(m)):
            if in_window(parse_dt(m.get("created_at")), month_start, daily_end):
                ff_mtd += 1
            continue
        created = parse_dt(m.get("created_at"))
        if created is None:
            continue
        in_mtd = in_window(created, month_start, daily_end)
        in_day = in_window(created, daily_start, daily_end)
        if _membership_is_paid(m):
            paid_mtd += 1 if in_mtd else 0
            paid_day += 1 if in_day else 0
        else:
            pend_mtd += 1 if in_mtd else 0
            pend_day += 1 if in_day else 0
    return {"paid_day": paid_day, "paid_mtd": paid_mtd,
            "pending_day": pend_day, "pending_mtd": pend_mtd,
            "friends_family_mtd": ff_mtd}


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------
def build_sms(prefix, label, calls, books, bookings, members, review_reqs=None):
    """Build the SMS digest body. prefix is "MBM daily" or "MBM weekly".
    calls/books are GA4 (period, mtd) tuples or None (skip the line).
    review_reqs is (week, all_time_total) from count_review_requests() or
    None (skip the line). When the digest moves to weekly cadence "MTD" still
    means month-to-date for the existing lines."""
    b_day, b_mtd = bookings
    lines = [f"{prefix} - {label}"]
    if calls is not None:
        lines.append(f"Phone clicks: {calls[0]} ({calls[1]} MTD)")
    if books is not None:
        lines.append(f"Booking clicks: {books[0]} ({books[1]} MTD)")
    lines.append(f"Initial consults: {b_day} ({b_mtd} MTD)")
    lines.append(f"Members: {members['paid_day']} ({members['paid_mtd']} MTD)")
    lines.append(f"Pending: {members['pending_day']} ({members['pending_mtd']} MTD)")
    if review_reqs is not None:
        lines.append(f"Review requests sent: {review_reqs[0]} ({review_reqs[1]} total)")
    return "\n".join(lines)


def build_email(prefix, label, month_label, calls, books, bookings, members, review_reqs=None):
    b_day, b_mtd = bookings
    subject = (
        f"MBM daily: {b_day} new consult{'s' if b_day != 1 else ''}, "
        f"{members['paid_day']} new member{'s' if members['paid_day'] != 1 else ''}"
    )
    rows = []
    if calls is not None:
        rows.append(("Phone clicks (website)", calls[0], calls[1]))
    if books is not None:
        rows.append(("Booking clicks (website)", books[0], books[1]))
    rows += [
        ("Initial consults booked", b_day, b_mtd),
        ("New members (enrolled + paid)", members["paid_day"], members["paid_mtd"]),
        ("Pending enrollments (awaiting payment)", members["pending_day"], members["pending_mtd"]),
    ]
    if review_reqs is not None:
        rows.append(("Review requests sent (Spruce)", review_reqs[0], review_reqs[1]))
    text = ["Mt. Baker Medical - daily numbers", f"{label}  |  Month to date: {month_label}", ""]
    for name, d, m in rows:
        text.append(f"  {name}: {d}   (MTD {m})")
    text += ["", "Counts only - no patient data in this email."]
    text_body = "\n".join(text)
    tr = "".join(
        f'<tr><td style="padding:8px 14px;font-size:14px;color:#1a1a1a;">{n}</td>'
        f'<td style="padding:8px 14px;font-size:18px;font-weight:700;color:#1a6b4a;text-align:right;">{d}</td>'
        f'<td style="padding:8px 14px;font-size:14px;color:#6b7280;text-align:right;">{m}</td></tr>'
        for n, d, m in rows)
    html_body = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background:#f0f4f1;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;"><tr><td align="center">'
        '<table width="100%" style="max-width:460px;background:#fff;border-radius:14px;overflow:hidden;">'
        '<tr><td style="background:#1a6b4a;padding:22px 28px;">'
        '<p style="margin:0;font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#a8d5be;">Daily Numbers</p>'
        '<p style="margin:4px 0 0;font-size:20px;font-weight:700;color:#fff;">Mt. Baker Medical</p></td></tr>'
        f'<tr><td style="padding:20px 28px 6px;"><p style="margin:0;font-size:13px;color:#6b7280;">{label}</p></td></tr>'
        '<tr><td style="padding:6px 14px 8px;"><table width="100%">'
        '<tr><td></td><td style="font-size:11px;color:#9ca3af;text-align:right;padding:0 14px;">NEW</td>'
        '<td style="font-size:11px;color:#9ca3af;text-align:right;padding:0 14px;">MONTH</td></tr>'
        f'{tr}</table></td></tr>'
        '<tr><td style="padding:10px 28px 24px;"><p style="margin:0;font-size:11px;color:#9ca3af;">Counts only - no patient data. Source: Hint.</p></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return subject, text_body, html_body


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def sms_recipients():
    return [normalize_e164(p) for p in SUMMARY_SMS_TO.split(",") if p.strip()]


def send_sms(text):
    recips = sms_recipients()
    if not recips:
        return None  # SMS channel not configured
    if DRY_RUN:
        log("[DRY_RUN] would SMS the digest to: " + ", ".join(r[-4:].rjust(4, '*') for r in recips))
        print("-" * 60 + f"\n(to {len(recips)} recipient(s))\n{text}\n" + "-" * 60)
        return True
    if not SPRUCE_API_KEY or not SPRUCE_INTERNAL_ENDPOINT_ID:
        log("Spruce not configured (SPRUCE_API_KEY / SPRUCE_INTERNAL_ENDPOINT_ID) - cannot SMS.")
        return False
    url = f"{SPRUCE_BASE_URL}/internalendpoints/{SPRUCE_INTERNAL_ENDPOINT_ID}/conversations"
    ok_all = True
    for r in recips:
        try:
            resp = http.post(url,
                headers={"Authorization": f"Bearer {SPRUCE_API_KEY}", "Content-Type": "application/json"},
                json={"destination": {"smsOrEmailEndpoint": r},
                      "message": {"body": [{"type": "text", "value": text}]}},
                timeout=15)
            if resp.status_code in (200, 201):
                log(f"summary SMS sent to ...{r[-4:]}")
            else:
                ok_all = False
                log(f"Spruce {resp.status_code} sending to ...{r[-4:]}: {resp.text[:160]}")
        except Exception as e:
            ok_all = False
            log(f"error sending SMS to ...{r[-4:]}: {e}")
    return ok_all


def send_email(subject, text_body, html_body):
    if not (SUMMARY_EMAIL_TO and RESEND_API_KEY):
        return None  # email channel not configured
    if DRY_RUN:
        log(f"[DRY_RUN] would email digest to {SUMMARY_EMAIL_TO}: {subject}")
        return True
    try:
        resp = http.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [e.strip() for e in SUMMARY_EMAIL_TO.split(",") if e.strip()],
                  "subject": subject, "html": html_body, "text": text_body},
            timeout=15)
        if resp.status_code in (200, 201):
            log(f"summary email sent to {SUMMARY_EMAIL_TO}")
            return True
        log(f"Resend {resp.status_code}: {resp.text[:160]}")
        return False
    except Exception as e:
        log(f"error sending summary email: {e}")
        return False


# ---------------------------------------------------------------------------
# Self-test (offline)
# ---------------------------------------------------------------------------
def selftest():
    now = datetime(2026, 6, 14, 9, 0, tzinfo=PACIFIC)
    d_start, d_end, m_start, label = day_bounds_pacific(now, "yesterday")
    assert label.startswith("Yesterday"), label
    assert d_start == datetime(2026, 6, 13, 0, 0, tzinfo=PACIFIC)
    assert m_start == datetime(2026, 6, 1, 0, 0, tzinfo=PACIFIC)
    assert normalize_e164("(360) 349-8094") == "+13603498094"
    assert normalize_e164("13603498094") == "+13603498094"

    def iso(dt):
        return dt.astimezone(timezone.utc).isoformat()

    appts = [
        {"id": "a1", "status": "confirmed", "created_at": iso(datetime(2026, 6, 13, 10, tzinfo=PACIFIC))},
        {"id": "a2", "status": "unconfirmed", "created_at": iso(datetime(2026, 6, 5, 10, tzinfo=PACIFIC))},
        {"id": "a3", "status": "cancelled", "created_at": iso(datetime(2026, 6, 13, 11, tzinfo=PACIFIC))},
        {"id": "a4", "status": "confirmed", "created_at": iso(datetime(2026, 5, 30, 10, tzinfo=PACIFIC))},
    ]
    assert count_bookings(appts, d_start, d_end, m_start) == (1, 2)

    mems = [
        {"id": "m1", "status": "active", "never_been_billed": False, "created_at": iso(datetime(2026, 6, 13, 12, tzinfo=PACIFIC))},
        {"id": "m2", "status": "active", "never_been_billed": True, "created_at": iso(datetime(2026, 6, 13, 13, tzinfo=PACIFIC))},
        {"id": "m3", "status": "active", "last_bill_amount_in_cents": 9900, "created_at": iso(datetime(2026, 6, 4, 9, tzinfo=PACIFIC))},
        {"id": "m4", "status": "cancelled", "never_been_billed": False, "created_at": iso(datetime(2026, 6, 13, 9, tzinfo=PACIFIC))},
        {"id": "m5", "status": "active", "never_been_billed": False, "created_at": iso(datetime(2026, 5, 28, 9, tzinfo=PACIFIC))},
        # Comp / Friends & Family: paid-looking, created in-window, but NOT an acquisition.
        # Must land in friends_family_mtd and never in paid_*. Regression guard for the
        # "digest said 14 new members, dashboard said 3" bug (the 11 F&F comps).
        {"id": "m6", "status": "active", "never_been_billed": False, "plan": {"name": "Friends & Family"},
         "created_at": iso(datetime(2026, 6, 13, 14, tzinfo=PACIFIC))},
        {"id": "m7", "status": "active", "never_been_billed": False, "plan": {"name": "Friends and Family - comp"},
         "created_at": iso(datetime(2026, 6, 4, 10, tzinfo=PACIFIC))},
    ]
    mc = count_members(mems, d_start, d_end, m_start)
    assert mc == {"paid_day": 1, "paid_mtd": 2, "pending_day": 1, "pending_mtd": 1,
                  "friends_family_mtd": 2}, mc
    assert mc["paid_mtd"] == 2, "F&F comps must never inflate the new-member count"

    # Digest with GA4 unavailable (the safe fallback shape):
    sms_no_ga4 = build_sms("MBM daily", label, None, None, (1, 2), mc)
    assert "Phone clicks" not in sms_no_ga4, sms_no_ga4
    assert "Booking clicks" not in sms_no_ga4, sms_no_ga4
    assert "Initial consults: 1 (2 MTD)" in sms_no_ga4, sms_no_ga4
    assert "Members: 1 (2 MTD)" in sms_no_ga4, sms_no_ga4
    assert "Pending: 1 (1 MTD)" in sms_no_ga4, sms_no_ga4

    # Digest with GA4 wired up (the target shape):
    sms = build_sms("MBM daily", label, calls=(5, 23), books=(8, 41), bookings=(1, 2), members=mc)
    assert "Phone clicks: 5 (23 MTD)" in sms, sms
    assert "Booking clicks: 8 (41 MTD)" in sms, sms
    assert "Initial consults: 1 (2 MTD)" in sms, sms
    # Line order: header, phone, booking, consults, members, pending.
    parts = sms.split("\n")
    assert parts[0].startswith("MBM daily"), parts
    assert parts[1].startswith("Phone clicks:"), parts
    assert parts[2].startswith("Booking clicks:"), parts
    assert parts[3].startswith("Initial consults:"), parts
    assert parts[4].startswith("Members:"), parts
    assert parts[5].startswith("Pending:"), parts

    # Email subject reflects the rename ("consult" not "booking"):
    subj, _, _ = build_email("MBM daily", label, "Jun", (5, 23), (8, 41), (1, 2), mc)

    # Weekly variant: prefix changes, "Week of" label, review_reqs line included.
    _, _, m_start_w, week_label = day_bounds_pacific(now, "last_7d")
    assert week_label.startswith("Week of "), week_label
    sms_w = build_sms("MBM weekly", week_label, (5, 23), (8, 41), (1, 2), mc, review_reqs=(8, 47))
    assert sms_w.startswith("MBM weekly - Week of "), sms_w
    assert "Review requests sent: 8 (47 total)" in sms_w, sms_w
    assert "consult" in subj, subj
    assert "booking" not in subj.lower(), subj

    # GA4 helper returns {} when no property id is set (silent skip).
    global GA4_PROPERTY_ID
    saved = GA4_PROPERTY_ID
    GA4_PROPERTY_ID = ""
    try:
        assert count_ga4_events(["phone_click"], d_start, d_end, m_start) == {}, "ga4 should no-op"
    finally:
        GA4_PROPERTY_ID = saved

    print("SELFTEST OK\n" + sms)
    return True


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Daily Hint bookings/members summary (SMS + optional email).")
    ap.add_argument("--dry-run", action="store_true", help="Print the digest, send nothing.")
    ap.add_argument("--no-send", action="store_true",
                    help="Do every pull and advance the consult tally, but send nothing. This is "
                         "the TALLY-KEEPER mode for the daily task now that the digest went weekly: "
                         "consult_count's month-to-date tally only advances on runs, so if nothing "
                         "runs daily the count silently under-reports. Same effect as --dry-run but "
                         "named for intent, so nobody later 'cleans up' a stray dry-run task.")
    ap.add_argument("--send", action="store_true", help="Force a real send (ignores DRY_RUN env).")
    ap.add_argument("--window", choices=["yesterday", "today", "24h", "last_7d"], default="yesterday",
                    help="What the daily number covers. Default: yesterday. Use last_7d for the weekly digest.")
    ap.add_argument("--selftest", action="store_true", help="Run offline logic checks and exit.")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        sys.exit(0)

    global DRY_RUN
    if args.send:
        DRY_RUN = False
    if args.dry_run:
        DRY_RUN = True
    # --no-send wins over --send: the daily tally-keeper must never text, even if a wrapper
    # or .env still forces sending.
    if args.no_send:
        DRY_RUN = True

    if not HINT_API_KEY:
        log("error: HINT_API_KEY not set. Aborting.")
        sys.exit(2)

    d_start, d_end, m_start, label = day_bounds_pacific(window=args.window)
    horizon = d_end + timedelta(days=HORIZON_DAYS)
    log(f"summary start: window={args.window} month_start={m_start.date()} send={'no' if DRY_RUN else 'yes'}")

    appts = fetch_appointments(m_start, horizon)
    mems = fetch_memberships(m_start)
    log(f"fetched {len(appts)} appointment(s), {len(mems)} membership(s) since month start")

    bookings = consult_count.tally(appts, d_start, d_end, m_start)
    members = count_members(mems, d_start, d_end, m_start)

    # GA4 click counts (top of funnel). Returns {} if not configured -- digest
    # falls back to Consults/Members/Pending only without erroring.
    ga4 = count_ga4_events(["phone_click", "booking_click"], d_start, d_end, m_start)
    calls = ga4.get("phone_click")
    books = ga4.get("booking_click")
    if not ga4:
        log("GA4 click lines unavailable; digest will ship without Phone/Booking clicks.")
    else:
        log(f"GA4 pulled: phone_click={calls}, booking_click={books}")

    prefix = "MBM weekly" if args.window == "last_7d" else "MBM daily"

    # Review-request tally (count of Spruce sends). Aggregate only -- no IDs.
    review_reqs = count_review_requests(d_start, d_end)
    log(f"review requests: week={review_reqs[0]}, all-time total={review_reqs[1]}")

    sms_text = build_sms(prefix, label, calls, books, bookings, members, review_reqs)
    subject, text_body, html_body = build_email(
        prefix, label, m_start.strftime("%b"), calls, books, bookings, members, review_reqs
    )

    r_sms = send_sms(sms_text)
    r_email = send_email(subject, text_body, html_body)

    if r_sms is None and r_email is None:
        log("error: no delivery channel configured. Set SUMMARY_SMS_TO (+ Spruce keys) "
            "and/or SUMMARY_EMAIL_TO (+ RESEND_API_KEY).")
        sys.exit(2)
    sys.exit(0 if (r_sms or r_email) else 1)


if __name__ == "__main__":
    main()
