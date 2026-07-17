#!/usr/bin/env python3
"""
_probe_hint_invoices2.py -- READ ONLY. Round 2: crack GET /api/provider/invoices
(list paid invoices) and learn the invoice object shape so we can trigger the
walk-in/IV review off a paid charge-item line.

PHI-SAFE: prints HTTP status, row COUNTS, and field NAMES only. Never prints a
patient value, name, amount, or memo. Charge-item catalog is config (full print
OK). No writes.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}
WALKIN = "item-69Twv7oo5mpg"; IV = "item-GpCfhrlwK9zB"

def get(path, params=None):
    try:
        r = http.get(f"{BASE}/api/provider/{path}", headers=H, params=params or {}, timeout=30)
        return r.status_code, r
    except Exception as e:
        return None, str(e)

def names_only(obj, depth=0, prefix=""):
    """Recursively print FIELD NAMES + types only (no values), for one object."""
    if isinstance(obj, dict):
        for k in sorted(obj.keys()):
            v = obj[k]
            t = type(v).__name__
            if isinstance(v, (dict, list)):
                n = len(v)
                print(f"    {prefix}{k}: {t}({n})")
                if isinstance(v, list) and v and isinstance(v[0], dict) and depth < 2:
                    names_only(v[0], depth+1, prefix+f"{k}[0].")
                elif isinstance(v, dict) and depth < 2:
                    names_only(v, depth+1, prefix+f"{k}.")
            else:
                print(f"    {prefix}{k}: {t}")

print(f"BASE={BASE}\n")

print("=== crack GET /invoices — find params that return rows ===")
matrix = [
    {"limit":5},
    {"limit":5,"status":"paid"},
    {"limit":5,"status":"open"},
    {"limit":5,"status":"closed"},
    {"limit":5,"paid":"true"},
    {"limit":5,"created_at[gte]":"2026-06-01"},
    {"limit":5,"paid_at[gte]":"2026-06-01"},
    {"limit":5,"sort":"-created_at"},
    {"limit":5,"offset":0},
]
sample = None
for params in matrix:
    code, r = get("invoices", params)
    n = None
    if code == 200:
        try:
            d = r.json(); rows = d if isinstance(d, list) else d.get("data", [])
            n = len(rows)
            if rows and sample is None:
                sample = rows[0]
        except Exception:
            n = "?"
    print(f"GET /invoices {json.dumps(params)} -> {code}  rows={n}")

if sample is not None:
    print("\n=== one invoice object: FIELD NAMES + types only (redacted) ===")
    names_only(sample)
    flat = json.dumps(sample)
    print("\n  quick checks:",
          "has 'patient'|'patient_id':", ("patient" in flat),
          "| has 'line_items'|'items':", ("line_items" in flat or '"items"' in flat),
          "| mentions a charge item id:", ("item-" in flat))
else:
    print("\n(no /invoices rows returned on any variant — will pivot to /payments or a webhook-cursor approach)")

print("\n=== /payments deeper: does a payment link to an invoice or patient? (names only) ===")
code, r = get("payments", {"limit":5})
if code == 200:
    try:
        rows = r.json(); rows = rows if isinstance(rows, list) else rows.get("data", [])
        print(f"rows={len(rows)}")
        if rows: names_only(rows[0])
    except Exception as e:
        print("parse err", e)

print("\n=== Walk-In + IV catalog prices (price_in_cents) ===")
code, r = get("charge_items", {"limit":200})
if code == 200:
    for it in (r.json() if isinstance(r.json(), list) else r.json().get("data", [])):
        if it.get("id") in (WALKIN, IV):
            print(f"  {it.get('id')}  {it.get('name')!r}  ${(it.get('price_in_cents') or 0)/100:.2f}  category={it.get('category')}")

print("\nDone (read-only).")
