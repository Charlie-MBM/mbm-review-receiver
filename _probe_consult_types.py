#!/usr/bin/env python3
"""Read-only probe: what distinguishes the different free-consult booking TYPES in
Hint, so the no-show follow-up can route a rebook link by type (concierge vs
ketamine vs ...). Patient info is masked - only config-level signals are printed.
Run:  py _probe_consult_types.py
"""
import os, re, json
import datetime as dt
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\-.\(\) ]{7,}\d")

def mask(s):
    if not s:
        return s
    s = EMAIL_RE.sub("<email>", s)
    s = PHONE_RE.sub("<phone>", s)
    return s

def desc_labels(desc):
    """Field labels in the booking description. Name/DOB/Phone/Email VALUES are
    redacted; a service/type/reason value is kept (it's a service name, not PII)."""
    if not desc:
        return []
    text = re.sub(r"<[^>]+>", "\n", desc)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        label, val = line.split(":", 1)
        label, val = label.strip(), val.strip()
        low = label.lower()
        if low in ("name", "first name", "last name", "dob", "date of birth", "phone", "mobile", "cell", "email", "e-mail"):
            out.append(f"{label}: <redacted>")
        elif any(k in low for k in ("service", "reason", "type", "appointment", "visit", "interest", "program", "plan", "booking", "what brings", "which")):
            out.append(f"{label}: {mask(val)[:70]}")
        else:
            out.append(f"{label}: {mask(val)[:40]}")
    return out

def type_signal_lines(desc):
    """Surface any non-PII heading/subject lines in the description (e.g. the
    'Free Consultation - Concierge Primary Care' subject), while dropping the
    Name/DOB/Phone/Email fields and the free-text focus answer (may contain health info)."""
    if not desc:
        return []
    text = re.sub(r"<[^>]+>", "\n", desc)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(k) for k in ("name:", "first name", "last name", "dob", "date of birth",
                                           "phone", "mobile", "cell", "email", "e-mail",
                                           "what would you like", "what brings", "anything")):
            continue
        out.append(mask(line)[:90])
    return out


print("=== 1) endpoint discovery: appointment types / services / booking pages ===")
for path in ["/api/provider/appointment_types", "/api/provider/services", "/api/provider/booking_pages",
             "/api/provider/locations", "/api/provider/practitioners", "/api/provider/providers",
             "/api/provider/calendars", "/api/provider/appointment_reasons"]:
    try:
        r = http.get(f"{BASE}{path}", headers=H, timeout=20)
        print(f"\n{path} -> {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            items = d if isinstance(d, list) else d.get("data", d)
            if isinstance(items, list):
                for it in items[:25]:
                    if isinstance(it, dict):
                        print("   ", {k: it.get(k) for k in ("id", "name", "slug", "url", "booking_url", "public_url", "type", "title", "duration") if it.get(k) is not None})
            elif isinstance(d, dict):
                print("   keys:", list(d.keys())[:25])
    except Exception as e:
        print(f"{path} -> ERR {e}")

print("\n\n=== 2) recent appointments: type signals (PII masked) ===")
today = dt.date.today()
start = (today - dt.timedelta(days=14)).isoformat()   # <=31d total window (Hint 400s past that)
end = (today + dt.timedelta(days=14)).isoformat()
r = http.get(f"{BASE}/api/provider/appointments", headers=H,
             params={"start_date": start, "end_date": end, "limit": 100}, timeout=30)
appts = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
print(f"{len(appts)} appointments {start}..{end}  (http {r.status_code})")

prov, loc, titles = Counter(), Counter(), Counter()
for a in appts:
    if not isinstance(a, dict):
        continue
    p = (a.get("provider") or {}).get("name") if isinstance(a.get("provider"), dict) else a.get("provider")
    l = (a.get("location") or {}).get("name") if isinstance(a.get("location"), dict) else a.get("location")
    prov[str(p)] += 1
    loc[str(l)] += 1
    titles[str(a.get("title"))] += 1
print("distinct providers:", dict(prov))
print("distinct locations:", dict(loc))
print("distinct titles  :", dict(titles))

print("\n-- per-appointment structure (first 8, masked) --")
for a in appts[:8]:
    if not isinstance(a, dict):
        continue
    p = (a.get("provider") or {}).get("name") if isinstance(a.get("provider"), dict) else a.get("provider")
    l = (a.get("location") or {}).get("name") if isinstance(a.get("location"), dict) else a.get("location")
    print(f"\nappt {a.get('id')} status={a.get('status')} start={a.get('start')}")
    print("  title:", a.get("title"), "| workflow_status:", a.get("workflow_status"))
    print("  host:", mask(str(a.get("host")))[:220])
    print("  TYPE-signal lines:", type_signal_lines(a.get("description")))

print("\n\n=== 3) single-appointment DETAIL endpoint (richer fields?) ===")
_fid = next((a.get("id") for a in appts if isinstance(a, dict)), None)
if _fid:
    rd = http.get(f"{BASE}/api/provider/appointments/{_fid}", headers=H, timeout=20)
    print(f"GET /appointments/{_fid} -> {rd.status_code}")
    if rd.status_code == 200:
        obj = rd.json()
        if isinstance(obj, dict):
            print("  detail keys:", list(obj.keys()))
            for k in ("title", "subject", "name", "type", "appointment_type", "reason",
                      "service", "booking_page", "booking", "label", "summary", "notes", "workflow_status"):
                if k in obj and obj.get(k) not in (None, "", []):
                    print(f"  {k}:", mask(str(obj.get(k)))[:140])
