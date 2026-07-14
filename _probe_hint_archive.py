#!/usr/bin/env python3
"""Can Hint's API archive / deactivate a patient? Tests ONLY on the authorized
synthetic dummy (pat-WwxwxOEdr9BI = ZZTEST NurtureCheck). Finds the status field,
tries several archive methods, then reverts the dummy to its original state.
No real patient record is touched.
Run:  py _probe_hint_archive.py
"""
import os
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
DUMMY = "pat-WwxwxOEdr9BI"   # synthetic test account (Charlie-authorized)


def get():
    r = http.get(f"{BASE}/api/provider/patients/{DUMMY}", headers=H, timeout=20)
    return r.status_code, (r.json() if r.status_code == 200 else r.text[:200])


sc, p = get()
print("GET dummy ->", sc)
if sc != 200:
    print(p); raise SystemExit
print("all patient keys:", list(p.keys()))
status_keys = [k for k in p.keys() if any(t in k.lower()
               for t in ("status", "active", "archiv", "state", "inactive", "deactiv"))]
orig = {k: p.get(k) for k in status_keys}
print("status-like fields (BEFORE):", orig)

# 1) Is PATCH /patients/{id} accepted at all? (no-op: write first_name back to itself)
rp = http.patch(f"{BASE}/api/provider/patients/{DUMMY}", headers=H,
                json={"first_name": p.get("first_name") or "ZZTEST"}, timeout=20)
print("\nPATCH (no-op first_name) ->", rp.status_code, "|", rp.text[:150])

# 2) Candidate archive methods (on the dummy) - report which the API accepts
tests = [
    ("PATCH status=archived", "patch", f"/api/provider/patients/{DUMMY}", {"status": "archived"}),
    ("PATCH status=inactive", "patch", f"/api/provider/patients/{DUMMY}", {"status": "inactive"}),
    ("PATCH archived=true",   "patch", f"/api/provider/patients/{DUMMY}", {"archived": True}),
    ("PATCH active=false",    "patch", f"/api/provider/patients/{DUMMY}", {"active": False}),
    ("POST /archive",         "post",  f"/api/provider/patients/{DUMMY}/archive", {}),
    ("POST /deactivate",      "post",  f"/api/provider/patients/{DUMMY}/deactivate", {}),
]
print("\n-- archive-method probes --")
for label, method, path, body in tests:
    try:
        rr = getattr(http, method)(f"{BASE}{path}", headers=H, json=body, timeout=20)
        print(f"  {label:22s} -> {rr.status_code} | {rr.text[:110]}")
    except Exception as e:
        print(f"  {label:22s} -> ERR {e}")

# 3) Best-effort REVERT: put the original status-field values back
if orig:
    rv = http.patch(f"{BASE}/api/provider/patients/{DUMMY}", headers=H, json=orig, timeout=20)
    print("\nREVERT (restore original status fields) ->", rv.status_code, "|", rv.text[:120])
for path in (f"/api/provider/patients/{DUMMY}/unarchive", f"/api/provider/patients/{DUMMY}/activate"):
    try:
        ra = http.post(f"{BASE}{path}", headers=H, json={}, timeout=20)
        print(f"REVERT try {path.split('/')[-1]} -> {ra.status_code}")
    except Exception:
        pass

sc2, p2 = get()
if sc2 == 200:
    print("\nstatus-like fields (AFTER revert):", {k: p2.get(k) for k in status_keys})
