#!/usr/bin/env python3
"""Probe Hint API: confirm membership status is retrievable (suppression
source of truth). Read-only. PII redacted."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
ENV = os.environ.get("HINT_ENV", "production")
BASE = "https://api.hint.com" if ENV == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}
print("HINT_ENV:", ENV, "BASE:", BASE)

# An inactive (Mark S.) and an active patient id from the Spruce probe
for label, pid in [("inactive-MarkS", "pat-28xvYLdRMBaQ"), ("active-KellyS", "pat-x6mlcomOtTBc")]:
    print(f"\n=== {label} {pid} ===")
    r = http.get(f"{BASE}/api/provider/patients/{pid}", headers=H, timeout=20)
    print("patient status:", r.status_code)
    if r.status_code == 200:
        p = r.json()
        keys = list(p.keys())
        print("patient keys:", keys)
        # show membership-relevant fields, redact name/contact
        for k in ("status", "membership_status", "active", "memberships", "membership", "payment_sources", "has_payment_source"):
            if k in p:
                v = p[k]
                print(f"  {k}:", json.dumps(v, default=str)[:600])
    else:
        print("  body:", r.text[:300])
    # memberships sub-resource
    rm = http.get(f"{BASE}/api/provider/patients/{pid}/memberships", headers=H, timeout=20)
    print("memberships endpoint status:", rm.status_code)
    if rm.status_code == 200:
        md = rm.json()
        items = md if isinstance(md, list) else md.get("data", md)
        if isinstance(items, list):
            print("  membership count:", len(items))
            if items:
                print("  membership[0] keys:", list(items[0].keys()))
                for k in ("status", "active", "plan_name", "ended_at", "started_at", "canceled_at"):
                    if k in items[0]:
                        print(f"    {k}:", items[0][k])
        else:
            print("  shape:", type(items))
