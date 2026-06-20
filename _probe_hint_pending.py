#!/usr/bin/env python3
"""Probe Hint API for the pending-membership nurture trigger (T5c):
- the dummy's pending membership object + where payment-source presence lives
- the memberships list endpoint (open gate 2) and how to find PENDING ones
Read-only. PII redacted."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}
DUMMY = "pat-WwxwxOEdr9BI"
MEM = "mem-NomErLrkhCXp"


def show(label, r, keys=None, full=False):
    print(f"\n=== {label} -> {r.status_code} ===")
    if r.status_code != 200:
        print("  body:", r.text[:300]); return None
    d = r.json()
    if isinstance(d, dict) and full:
        print(json.dumps(d, default=str)[:1500])
    return d


# 1) dummy patient object: membership_status, memberships[], any payment fields
r = http.get(f"{BASE}/api/provider/patients/{DUMMY}", headers=H, timeout=20)
p = show("patient (dummy)", r)
if p:
    print("  membership_status:", p.get("membership_status"))
    print("  patient keys w/ 'pay' or 'card' or 'source':",
          [k for k in p.keys() if any(t in k.lower() for t in ("pay", "card", "source", "billing", "autopay"))])
    mems = p.get("memberships") or []
    print("  memberships count:", len(mems))
    for m in mems:
        if isinstance(m, dict):
            print("  membership obj keys:", list(m.keys()))
            print("  membership redacted:", json.dumps({k: m.get(k) for k in m.keys() if k not in ('payer',)}, default=str)[:900])

# 2) membership sub-resource / direct fetch
for path in [f"/api/provider/patients/{DUMMY}/memberships", f"/api/provider/memberships/{MEM}", f"/api/provider/memberships?patient_id={DUMMY}"]:
    r = http.get(f"{BASE}{path}", headers=H, timeout=20)
    d = show(f"GET {path}", r)
    if d and isinstance(d, (list, dict)):
        items = d if isinstance(d, list) else d.get("data", [d])
        if items and isinstance(items[0], dict):
            print("  first item keys:", list(items[0].keys()))
            print("  first item:", json.dumps(items[0], default=str)[:1200])

# 3) memberships LIST endpoint (gate 2) — find pending ones
for path in ["/api/provider/memberships", "/api/provider/memberships?status=pending", "/api/provider/memberships?updated_at_after=2026-06-01T00:00:00Z"]:
    r = http.get(f"{BASE}{path}", headers=H, timeout=30)
    d = show(f"LIST {path}", r)
    if d is not None:
        items = d if isinstance(d, list) else d.get("data", d if isinstance(d, list) else [])
        if isinstance(items, list):
            print("  count:", len(items))
            from collections import Counter
            print("  status tally:", Counter((m.get("status") if isinstance(m, dict) else None) for m in items))
            # show any pending
            for m in items:
                if isinstance(m, dict) and (m.get("status") == "pending"):
                    print("  PENDING:", json.dumps({k: m.get(k) for k in ("id","patient_id","status","start_date","end_date","plan") }, default=str)[:400])

# 4) payment source / payment options endpoints
for path in [f"/api/provider/patients/{DUMMY}/payment_sources", f"/api/provider/patients/{DUMMY}/payment_methods", f"/api/provider/patients/{DUMMY}/payment_options", f"/api/provider/payment_sources?patient_id={DUMMY}"]:
    r = http.get(f"{BASE}{path}", headers=H, timeout=20)
    show(f"PAYMENT {path}", r, full=True)
