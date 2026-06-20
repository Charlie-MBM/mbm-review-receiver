#!/usr/bin/env python3
"""LIVE cancel test on the ZZTEST dummy membership (Charlie-authorized 2026-06-11).
Verifies the cancel endpoint + cancellation_reason enum AND clears the owed $500
dummy bill. Targets ONLY the dummy mem-NomErLrkhCXp."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
MEM = "mem-NomErLrkhCXp"  # ZZTEST NurtureCheck only

# 1) fetch to get the exact bill_date (cancel end_date must line up with it)
m = http.get(f"{BASE}/api/provider/memberships/{MEM}", headers={"Authorization": H["Authorization"]}, timeout=20).json()
m = m[0] if isinstance(m, list) else m
print("before: status=", m.get("status"), "bill_date=", m.get("bill_date"),
      "upcoming $=", [b.get("amount_in_cents") for b in (m.get("upcoming_bills") or [])])
bill_date = m.get("bill_date") or m.get("start_date")

# 2) attempt cancel with documented body
body = {"end_date": bill_date, "cancellation_reason": {"name": "Other"},
        "cancellation_reason_other": "ZZTEST cleanup + T5d cancel-path verification"}
r = http.post(f"{BASE}/api/provider/memberships/{MEM}/cancel", headers=H, json=body, timeout=25)
print("cancel status:", r.status_code)
print("cancel response:", r.text[:500])

# 3) verify
v = http.get(f"{BASE}/api/provider/memberships/{MEM}", headers={"Authorization": H["Authorization"]}, timeout=20)
if v.status_code == 200:
    vm = v.json()
    vm = vm[0] if isinstance(vm, list) else vm
    print("after: status=", vm.get("status"), "end_date=", vm.get("end_date"),
          "upcoming $=", [b.get("amount_in_cents") for b in (vm.get("upcoming_bills") or [])])
else:
    print("after GET:", v.status_code)
