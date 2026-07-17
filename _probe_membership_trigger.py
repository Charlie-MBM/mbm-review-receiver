#!/usr/bin/env python3
"""
_probe_membership_trigger.py -- READ ONLY. Validate the "membership type as the
walk-in/IV review trigger" idea. Lists the PLAN catalog (config, non-PHI) to see
if a Walk-In / IV plan exists (or needs creating), and prints the membership
object's field names + the date-filter behavior so we know the exact trigger
query. PHI-SAFE: plan catalog is config; membership rows print field NAMES +
counts only, no patient values. No writes.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}

def get(path, params=None):
    try:
        r = http.get(f"{BASE}/api/provider/{path}", headers=H, params=params or {}, timeout=30)
        return r.status_code, r
    except Exception as e:
        return None, str(e)

print(f"BASE={BASE}\n")

print("=== PLAN catalog (config, non-PHI) — is there a Walk-In / IV plan? ===")
found = False
for path in ["plans", "membership_types", "membership_plans"]:
    code, r = get(path, {"limit": 200})
    if code == 200:
        found = True
        items = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
        print(f"GET /{path} -> 200  ({len(items)} plans)")
        for it in items:
            if isinstance(it, dict):
                print(f"   id={it.get('id')}  name={it.get('name')!r}  type={it.get('plan_type') or it.get('type')}  amount={it.get('amount') or it.get('price_in_cents')}")
    else:
        print(f"GET /{path} -> {code}")

print("\n=== membership object shape (field NAMES only) + date-filter check ===")
code, r = get("memberships", {"limit": 1})
if code == 200:
    d = r.json(); rows = d if isinstance(d, list) else d.get("data", [])
    print(f"GET /memberships?limit=1 -> 200 rows={len(rows)}")
    if rows and isinstance(rows[0], dict):
        for k in sorted(rows[0].keys()):
            v = rows[0][k]; t = type(v).__name__
            print(f"   {k}: {t}" + (f"({len(v)})" if isinstance(v,(list,dict)) else ""))
# does created_at filter + status filter work (for the "since last run" trigger)?
for params in [{"limit":1,"created_at[gte]":"2026-06-01"}, {"limit":1,"status":"active"}, {"limit":1,"status":"pending"}]:
    code, r = get("memberships", params)
    n = None
    if code==200:
        d=r.json(); n=len(d if isinstance(d,list) else d.get("data",[]))
    print(f"GET /memberships {json.dumps(params)} -> {code} rows={n}")

print("\nDone (read-only).")
