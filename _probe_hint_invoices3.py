#!/usr/bin/env python3
"""
_probe_hint_invoices3.py -- READ ONLY, final API probe. Is a paid Walk-In/IV
charge reachable WITHOUT a pollable invoices endpoint? Checks: (a) charges/
balance embedded on the patient object, (b) patient-scoped invoice via query
param, (c) a few remaining endpoint names.

PHI-SAFE: uses ONLY Charlie's own record (pat-z7Pu6cu2FtQg). Prints field NAMES
+ types only (no values) except item ids on HIS record. No writes.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}
ME   = "pat-z7Pu6cu2FtQg"

def get(path, params=None):
    try:
        r = http.get(f"{BASE}/api/provider/{path}", headers=H, params=params or {}, timeout=30)
        return r.status_code, r
    except Exception as e:
        return None, str(e)

print(f"BASE={BASE}\n")

print("=== patient object: top-level field names (Charlie's own record) ===")
code, r = get(f"patients/{ME}")
if code == 200:
    d = r.json()
    for k in sorted(d.keys()):
        v = d[k]
        t = type(v).__name__
        extra = f"({len(v)})" if isinstance(v,(list,dict)) else ""
        billing = any(w in k.lower() for w in ["charg","invoic","balanc","bill","payment","transaction","ledger"])
        print(f"  {'>>' if billing else '  '} {k}: {t}{extra}")
        if billing and isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"        item fields: {sorted(v[0].keys())}")
else:
    print("  GET failed:", code)

print("\n=== patient-scoped invoice via query param ===")
for params in [{"patient_id":ME,"limit":5},{"patient":ME,"limit":5},{"patient_id":ME,"status":"paid","limit":5}]:
    code, r = get("invoices", params)
    n=None
    if code==200:
        d=r.json(); rows=d if isinstance(d,list) else d.get("data",[])
        n=len(rows)
    print(f"  GET /invoices {json.dumps(params)} -> {code} rows={n}")

print("\n=== remaining endpoint-name guesses ===")
for path in ["billing","collections","charge_captures","invoice","ledger_entries","line_items"]:
    code, r = get(path, {"limit":1})
    print(f"  GET /{path} -> {code}")

print("\nDone (read-only).")
