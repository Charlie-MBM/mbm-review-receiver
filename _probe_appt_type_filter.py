#!/usr/bin/env python3
"""Read-only: does GET /api/provider/appointments accept an appointment-type filter?
If one param name segments the counts (each type < unfiltered, and they roughly add
up to it), we can recover a no-show's consult type by which query returns it - which
unlocks per-type rebook links. No PII printed (counts only)."""
import os
import datetime as dt
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}

today = dt.date.today()
start = (today - dt.timedelta(days=14)).isoformat()
end = (today + dt.timedelta(days=14)).isoformat()

APPTYS = {
    "concierge": "appty-4dd0863cd8a5b6e3",
    "glp1":      "appty-b200b93fd813cceb",
    "ketamine":  "appty-05356b0756ee1adc",
    "hormone":   "appty-11ea2e8a6a5b9970",
}
PARAM_NAMES = ["appointment_type", "appointment_type_id", "appointment-type", "type", "appointment_type_ids[]"]

def count(params):
    try:
        r = http.get(f"{BASE}/api/provider/appointments", headers=H, params=params, timeout=30)
        d = r.json()
        items = d if isinstance(d, list) else d.get("data", [])
        return r.status_code, (len(items) if isinstance(items, list) else "?")
    except Exception as e:
        return "ERR", str(e)[:60]

base = {"start_date": start, "end_date": end, "limit": 100}
print("window", start, "..", end)
print("UNFILTERED:", count(base))
print()
for pk in PARAM_NAMES:
    print(f"--- param name: {pk} ---")
    for name, aid in APPTYS.items():
        p = dict(base); p[pk] = aid
        print(f"   {name:10s} {aid} -> {count(p)}")
    print()
