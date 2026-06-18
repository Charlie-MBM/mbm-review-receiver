#!/usr/bin/env python3
"""
send_daily_summary.py - Daily ops digest for Mt. Baker Medical.
================================================================
Sends Charlie + James a once-a-day, COUNTS-ONLY summary of:

  * New bookings   - appointments newly created in Hint (yesterday + month-to-date)
  * New members    - memberships newly enrolled AND paid (yesterday + month-to-date)
  * Pending enroll - memberships created but not yet billed/confirmed (Auto Confirm
                     is OFF, so these are awaiting staff confirmation + payment)

Primary channel is SMS via Spruce (already configured + BAA-covered, same line the
review poller uses). Email via Resend is an optional secondary channel.

WHY THIS IS SAFE (no PHI)
-------------------------
The message is ONLY integer counts -- no names, no patient IDs, no contact info,
nothing that identifies a person or that they used a health service. It is not
PHI / not WA-MHMD consumer health data. Do NOT add patient-identifying detail to
this digest.

DATA SOURCES (Hint Partner API, practices Bearer key - same key the review poller uses)
---------------------------------------------------------------------------------------
  GET /api/provider/appointments  (start_date/end_date window, paginated)
      -> id, start, end, status, created_at, ...  ("new bookings")
      Swept across current+upcoming windows and bucketed by created_at, because the
      endpoint filters by APPOINTMENT date, not creation date. Live appointments
      come back with title=null, so we cannot split consult vs member visit --
      "new bookings" = all newly-created appointments.
  GET /api/provider/memberships   (created_at[gte] filter, paginated)
      -> id, created_at, status, enrollment_status, never_been_billed,
         last_bill_amount_in_cents, ...
      "New member" = created in window AND never_been_billed == False (a charge has
      run = payment collected) AND status not cancelled.
      "Pending"    = created in window, not cancelled, never billed yet.

RUN
---
  py send_daily_summary.py --dry-run     # print the digest, send nothing
  py send_daily_summary.py --send        # force a real send (ignores DRY_RUN env)
  py send_daily_summary.py               # honors DRY_RUN env (.env)
  py send_daily_summary.py --window 24h  # daily number = rolling last 24h
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
  DRY_RUN                  true|false
"""

import argparse
import os
import sys
import consult_count
from datetime import datetime, timedelta, timezone

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
    paid_day = paid_mtd = pend_day = pend_mtd = 0
    for m in memberships:
        status = (m.get("status") or m.get("enrollment_status") or "").lower()
        if status in DEAD_MEMBERSHIP:
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
            "pending_day": pend_day, "pending_mtd": pend_mtd}


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------
def build_sms(label, bookings, members):
    b_day, b_mtd = bookings
    return (
        f"MBM daily - {label}\n"
        f"Bookings: {b_day} ({b_mtd} MTD)\n"
        f"Members: {members['paid_day']} ({members['paid_mtd']} MTD)\n"
        f"Pending: {members['pending_day']} ({members['pending_mtd']} MTD)"
    )


def build_email(label, month_label, bookings, members):
    b_day, b_mtd = bookings
    subject = (
        f"MBM daily: {b_day} new booking{'s' if b_day != 1 else ''}, "
        f"{members['paid_day']} new member{'s' if members['paid_day'] != 1 else ''}"
    )
    rows = [
        ("New bookings", b_day, b_mtd),
        ("New members (enrolled + paid)", members["paid_day"], members["paid_mtd"]),
        ("Pending enrollments (awaiting payment)", members["pending_day"], members["pending_mtd"]),
    ]
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
    ]
    mc = count_members(mems, d_start, d_end, m_start)
    assert mc == {"paid_day": 1, "paid_mtd": 2, "pending_day": 1, "pending_mtd": 1}, mc

    sms = build_sms(label, (1, 2), mc)
    assert "Bookings: 1 (2 MTD)" in sms and "Members: 1 (2 MTD)" in sms, sms
    print("SELFTEST OK\n" + sms)
    return True


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Daily Hint bookings/members summary (SMS + optional email).")
    ap.add_argument("--dry-run", action="store_true", help="Print the digest, send nothing.")
    ap.add_argument("--send", action="store_true", help="Force a real send (ignores DRY_RUN env).")
    ap.add_argument("--window", choices=["yesterday", "today", "24h"], default="yesterday",
                    help="What the daily number covers. Default: yesterday.")
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

    sms_text = build_sms(label, bookings, members)
    subject, text_body, html_body = build_email(label, m_start.strftime("%b"), bookings, members)

    r_sms = send_sms(sms_text)
    r_email = send_email(subject, text_body, html_body)

    if r_sms is None and r_email is None:
        log("error: no delivery channel configured. Set SUMMARY_SMS_TO (+ Spruce keys) "
            "and/or SUMMARY_EMAIL_TO (+ RESEND_API_KEY).")
        sys.exit(2)
    sys.exit(0 if (r_sms or r_email) else 1)


if __name__ == "__main__":
    main()
