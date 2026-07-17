#!/usr/bin/env python3
"""
_probe_hint_invoices.py -- READ ONLY Hint API probe for the walk-in/IV review
trigger (answers Q1-Q3 of WALKIN_REVIEW_WIRING_DESIGN.md).

Finds: (1) whether a pollable invoices/charges list endpoint exists + its date
filters, (2) whether line items expose charge-item id/name/amount + patient_id +
paid status, (3) the stable charge-item ids for Walk-In and IV services.

PHI-SAFE: service/charge-item CATALOG is practice config -> printed in full.
Patient-scoped calls use ONLY Charlie's authorized record (pat-z7Pu6cu2FtQg).
Practice-wide list endpoints print status + field names + count ONLY, never
patient values. No writes. Safe to paste the output back.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}
ME   = "pat-z7Pu6cu2FtQg"   # Charlie's own authorized test record

def get(path, params=None):
    url = f"{BASE}/api/provider/{path.lstrip('/')}"
    try:
        r = http.get(url, headers=H, params=params or {}, timeout=30)
        return r.status_code, r
    except Exception as e:
        return None, str(e)

def shape(r):
    try:
        d = r.json()
    except Exception:
        return f"(non-json {len(getattr(r,'text',''))}b)"
    if isinstance(d, list):
        return {"list_len": len(d), "item_fields": sorted(d[0].keys()) if d and isinstance(d[0], dict) else None}
    if isinstance(d, dict):
        keys = sorted(d.keys())
        inner = d.get("data")
        if isinstance(inner, list):
            return {"dict_keys": keys, "data_len": len(inner), "data_item_fields": sorted(inner[0].keys()) if inner and isinstance(inner[0], dict) else None}
        return {"dict_keys": keys}
    return type(d).__name__

print(f"BASE={BASE}\n")

print("=== 1) CATALOG endpoints (practice config, non-PHI) -> find Walk-In + IV item ids ===")
for path in ["charge_items", "products", "services", "catalog_items", "offerings", "items"]:
    code, r = get(path, {"limit": 200})
    print(f"GET /{path} -> {code}  {shape(r) if code==200 else ''}")
    if code == 200:
        try:
            d = r.json(); items = d if isinstance(d, list) else d.get("data", [])
            for it in items:
                if isinstance(it, dict):
                    nm = (it.get("name") or it.get("title") or "")
                    amt = it.get("amount") or it.get("price") or it.get("default_amount")
                    print(f"    id={it.get('id')}  name={nm!r}  amount={amt}")
        except Exception as e:
            print("    parse err:", e)

print("\n=== 2) practice-wide INVOICE/CHARGE list endpoints (status+fields+count ONLY, redacted) ===")
for path in ["invoices", "charges", "transactions", "payments"]:
    hit = False
    for params in [{"limit": 1}, {"limit": 1, "status": "paid"},
                   {"limit": 1, "paid_at[gte]": "2026-07-01"}, {"limit": 1, "created_at[gte]": "2026-07-01"}]:
        code, r = get(path, params)
        print(f"GET /{path} {json.dumps(params)} -> {code}  {shape(r) if code==200 else ''}")
        if code == 200:
            hit = True
            break
    if not hit:
        print(f"    (/{path}: no 200 on any variant)")

print("\n=== 3) per-patient subresource (Charlie's OWN record only) ===")
for path in [f"patients/{ME}/invoices", f"patients/{ME}/charges", f"patients/{ME}/transactions"]:
    code, r = get(path)
    print(f"GET /{path} -> {code}  {shape(r) if code==200 else ''}")
    if code == 200:
        try:
            d = r.json(); items = d if isinstance(d, list) else d.get("data", [])
            if items and isinstance(items[0], dict):
                li = items[0].get("line_items") or items[0].get("items") or items[0].get("charges")
                if isinstance(li, list) and li and isinstance(li[0], dict):
                    print("    line_item fields:", sorted(li[0].keys()))
        except Exception as e:
            print("    parse err:", e)

print("\nDone (read-only).")
