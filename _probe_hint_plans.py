#!/usr/bin/env python3
"""Read-only: list Hint plans + dump the schema of a simple existing one, so a
$0 Friends & Family plan can be created with the right billing fields (or we fall
back to the Hint UI). Config data only - no patient PHI. Run: py _probe_hint_plans.py
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}

for path in ["/api/provider/plans", "/api/provider/products"]:
    r = http.get(f"{BASE}{path}", headers=H, timeout=20)
    print(f"\nGET {path} -> {r.status_code}")
    if r.status_code != 200:
        print("  body:", r.text[:150])
        continue
    d = r.json()
    plans = d if isinstance(d, list) else d.get("data", d if isinstance(d, list) else [])
    if not isinstance(plans, list):
        print("  keys:", list(d.keys())[:20] if isinstance(d, dict) else type(d))
        continue
    print(f"  count: {len(plans)}")
    for p in plans[:12]:
        if isinstance(p, dict):
            print("   -", {k: p.get(k) for k in ("id", "name", "rate_in_cents", "period_in_months",
                                                 "status", "plan_type", "registration_fee_in_cents") if k in p})
    if plans and isinstance(plans[0], dict):
        print("\n  FULL schema of first plan (field list):")
        print("   ", list(plans[0].keys()))
        # dump a representative simple plan fully (config only)
        print("\n  FULL first plan object:")
        print("   ", json.dumps(plans[0], default=str)[:1500])
