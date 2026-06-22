"""
Diagnostic ONLY. Inspects the Hint /appointments endpoint to design the new
review trigger. Prints ONLY: counts, field NAMES, status values, and
appointment-TYPE names/ids (none of which are PHI). Never prints patient names,
DOB, contacts, or ids. Run:  py _diag_appointments.py
"""
import os
import collections
import datetime
import requests

BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
KEY = os.environ["HINT_API_KEY"]
URL = f"{BASE}/api/provider/appointments"
H = {"Authorization": f"Bearer {KEY}"}

end = datetime.date.today()
start = end - datetime.timedelta(days=30)  # endpoint caps range at 31 days

out, offset = [], 0
while True:
    r = requests.get(URL, headers=H, params={
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "limit": 100, "offset": offset,
    }, timeout=30)
    if r.status_code != 200:
        print("HTTP", r.status_code, "->", r.text[:200]); break
    b = r.json()
    b = b if isinstance(b, list) else b.get("data", [])
    out += b
    if len(b) < 100:
        break
    offset += 100

print(f"window {start}..{end}  appointments fetched (paginated): {len(out)}")
if not out:
    raise SystemExit

print("appointment field names:", sorted(out[0].keys()))
print("status counts:", dict(collections.Counter((a.get("status") or "?").lower() for a in out)))

# appointment-type identification (names/ids are not PHI) — find the free consult
for key in ("appointment_type", "appointment_type_id", "type", "name", "title", "kind", "reason"):
    vals = collections.Counter()
    present = False
    for a in out:
        v = a.get(key)
        if v is None:
            continue
        present = True
        if isinstance(v, dict):
            v = v.get("name") or v.get("id")
        vals[str(v)] += 1
    if present:
        print(f"distinct {key}:", dict(vals))

# attendee / patient nesting (KEYS only, no values)
for a in out:
    ats = a.get("attendees") or []
    if ats:
        print("attendee keys:", sorted(ats[0].keys()))
        print("attendee.patient keys:", sorted((ats[0].get("patient") or {}).keys()))
        break

pids = set()
for a in out:
    for at in (a.get("attendees") or []):
        pid = (at.get("patient") or {}).get("id")
        if pid:
            pids.add(pid)
print("distinct patient ids in window (count only):", len(pids))
