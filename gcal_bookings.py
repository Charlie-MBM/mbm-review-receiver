#!/usr/bin/env python3
"""
gcal_bookings.py - Read mbm-book Google Calendar bookings (attribution helper)
==============================================================================
Mt. Baker Medical - nurture layer (mbm-review-receiver). ADDITIVE, used only by
the ads-attribution path in send_nurture_sequence.py (gated by ADS_ATTRIB_ENABLED).

Self-contained on purpose: the two repos (mbm-hint-enrollment, mbm-review-receiver)
do NOT share code, so this mirrors - it does NOT import - the SA-JWT + events.list
approach from the consult poller (send_consult_intake.py). Read-only calendar
access; the service account needs only reader on Dr. Scribner's calendar.

Auth: prefers `google-auth` (already installed in this repo for the GA4 digest);
falls back to a hand-rolled RS256 service-account JWT via `cryptography` (same
approach as the consult poller), self-installing cryptography if absent.

NO PHI leaves here except back to the caller in-process. Nothing is uploaded
anywhere - this module only READS the calendar.
"""

import json
import logging
from datetime import timedelta
from pathlib import Path

try:
    import requests as http
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests", "--break-system-packages", "-q"])
    import requests as http

log = logging.getLogger("gcal_bookings")

GCAL_SOURCE_TAG = "mbm-book"
GOOGLE_TOKEN_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"  # read-only
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Google click-id keys as written into extendedProperties.private by the Worker
# (Attribution layer, ARCHITECTURE.md). Priority order for which id to report.
CLICK_ID_PRIORITY = ("gclid", "wbraid", "gbraid")

# Module-global access-token cache (mirrors the Worker / consult poller).
_TOKEN = {"access_token": None, "exp": 0.0}


def _b64url(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _token_via_google_auth(sa_key_file: str):
    """Preferred: reuse google-auth (already present for the GA4 digest). Returns
    an access token or None if google-auth is unavailable / fails."""
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GAuthRequest
    except ImportError:
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_key_file, scopes=[GOOGLE_TOKEN_SCOPE])
        creds.refresh(GAuthRequest())
        return creds.token
    except Exception as e:
        log.warning(f"gcal: google-auth token path failed ({e}); trying cryptography fallback")
        return None


def _token_via_cryptography(sa_key_file: str):
    """Fallback: hand-rolled RS256 SA JWT bearer grant (same as the consult poller).
    Self-installs cryptography if missing. Returns an access token or None."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        try:
            import subprocess
            subprocess.run(["pip", "install", "cryptography", "--break-system-packages", "-q"])
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except Exception as e:
            log.error(f"gcal: needs google-auth or cryptography (pip install cryptography): {e}")
            return None
    import time
    try:
        sa = json.loads(Path(sa_key_file).read_text())
    except Exception as e:
        log.error(f"gcal: could not read SA key file {sa_key_file}: {e}")
        return None
    try:
        iat = int(time.time())
        exp = iat + 3600
        header = {"alg": "RS256", "typ": "JWT"}
        claim = {"iss": sa.get("client_email"), "scope": GOOGLE_TOKEN_SCOPE,
                 "aud": GOOGLE_TOKEN_URI, "iat": iat, "exp": exp}
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            + "." + _b64url(json.dumps(claim, separators=(",", ":")).encode("utf-8"))
        ).encode("ascii")
        key = serialization.load_pem_private_key(sa["private_key"].encode("utf-8"), password=None)
        sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = signing_input.decode("ascii") + "." + _b64url(sig)
        r = http.post(GOOGLE_TOKEN_URI, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion}, timeout=20)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        log.error(f"gcal: cryptography token path failed: {e}")
        return None


def _access_token(sa_key_file: str):
    import time
    now_ts = time.time()
    if _TOKEN["access_token"] and now_ts < _TOKEN["exp"] - 300:
        return _TOKEN["access_token"]
    if not sa_key_file:
        log.warning("gcal: GOOGLE_SA_KEY_FILE not set - cannot read bookings")
        return None
    token = _token_via_google_auth(sa_key_file) or _token_via_cryptography(sa_key_file)
    if token:
        _TOKEN["access_token"] = token
        _TOKEN["exp"] = now_ts + 3600  # conservative; google-auth manages its own expiry too
    return token


def fetch_mbm_book_events(now, calendar_id: str, sa_key_file: str,
                          lookback_days: int, lookahead_days: int = 1):
    """events.list on the practice calendar filtered to mbm-book web bookings within
    [now - lookback_days, now + lookahead_days]. Returns a list of raw event dicts,
    or None on a hard auth/token failure (so the caller can retry rather than treat
    it as 'no match')."""
    if not calendar_id:
        log.warning("gcal: GCAL_CALENDAR_ID not set - cannot read bookings")
        return None
    token = _access_token(sa_key_file)
    if not token:
        return None
    import urllib.parse
    cal = urllib.parse.quote(calendar_id, safe="")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
    time_min = (now - timedelta(days=lookback_days)).isoformat()
    time_max = (now + timedelta(days=lookahead_days)).isoformat()
    out, page_token, failed = [], None, False
    while True:
        params = {
            "privateExtendedProperty": f"source={GCAL_SOURCE_TAG}",
            "timeMin": time_min, "timeMax": time_max,
            "singleEvents": "true", "showDeleted": "false",
            "orderBy": "startTime", "maxResults": 250,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            r = http.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error(f"gcal: events.list failed: {e}")
            failed = True
            break
        out.extend(data.get("items", []) or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    if failed and not out:
        return None
    return out


def _digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def _priv(ev: dict) -> dict:
    return ((ev.get("extendedProperties") or {}).get("private") or {})


def find_booking_for_phone(events, phone_e164: str):
    """The most recent mbm-book event whose private.phone matches phone_e164
    (compared on the trailing 10 digits, tolerant of format). None if no match."""
    if not events or not phone_e164:
        return None
    want = _digits(phone_e164)[-10:]
    if not want:
        return None
    best, best_key = None, None
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        ph = _digits(_priv(ev).get("phone"))[-10:]
        if ph and ph == want:
            key = (_priv(ev).get("booked_at") or (ev.get("start") or {}).get("dateTime") or "")
            if best is None or key > best_key:
                best, best_key = ev, key
    return best


def extract_ga_cid(ev: dict):
    """The GA4 client_id (ga_cid) the Worker stored on the booking, or None. Used as
    the GA4 Measurement Protocol client_id so the offline signup event stitches to the
    same GA4 user as the original booking session."""
    if not ev:
        return None
    v = (_priv(ev).get("ga_cid") or "").strip()
    return v or None


def extract_click_id(ev: dict):
    """If the matched booking carries a Google click id, return
    {click_id_type, click_id, gcal_event_id, booked_at}; else None (no attributable
    click - e.g. an organic/direct booking)."""
    if not ev:
        return None
    p = _priv(ev)
    for k in CLICK_ID_PRIORITY:
        v = (p.get(k) or "").strip()
        if v:
            return {
                "click_id_type": k,
                "click_id": v,
                "gcal_event_id": ev.get("id"),
                "booked_at": p.get("booked_at") or (ev.get("start") or {}).get("dateTime"),
            }
    return None
