#!/usr/bin/env python3
"""Confirm the payment-source discriminator and enumerate the live pending set.
Read-only. PII redacted (first name + last initial only)."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}


def rname(s):
    s = (s or "").strip()
    p = s.split()
    return (p[0] + (" " + p[-1][0] + "." if len(p) > 1 else "")) if p else "(none)"


def payment_methods(pid):
    r = http.get(f"{BASE}/api/provider/patients/{pid}/payment_methods", headers=H, timeout=20)
    if r.status_code != 200:
        return f"HTTP {r.status_code}", None
    d = r.json()
    items = d if isinstance(d, list) else d.get("data", d)
    return ("list", items)


# Enumerate all memberships; for pending ones, resolve patient + payment methods.
r = http.get(f"{BASE}/api/provider/memberships", headers=H, timeout=30)
mems = r.json()
mems = mems if isinstance(mems, list) else mems.get("data", [])
print("total memberships:", len(mems))

# Pick one ACTIVE membership's patient to confirm a card shows up
active = [m for m in mems if m.get("status") == "active"]
pending = [m for m in mems if m.get("status") == "pending"]


def patient_of(m):
    # LIST view: patient under membership_patients[].patient ; patient_id may be null
    mps = m.get("membership_patients") or []
    if mps and isinstance(mps[0], dict):
        pt = mps[0].get("patient") or {}
        return pt.get("id"), pt.get("name")
    return m.get("patient_id"), None


print("\n--- ACTIVE sample: does payment_methods show a card? ---")
for m in active[:2]:
    pid, nm = patient_of(m)
    kind, items = payment_methods(pid) if pid else ("no-pid", None)
    n = len(items) if isinstance(items, list) else "?"
    print(f"  {rname(nm)} {pid} status=active -> payment_methods: {kind}, count={n}")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        print("    pm item keys:", list(items[0].keys()))

print("\n--- PENDING set (the nurture candidates / back-scan) ---")
for m in pending:
    pid, nm = patient_of(m)
    kind, items = payment_methods(pid) if pid else ("no-pid", None)
    n = len(items) if isinstance(items, list) else "?"
    has_card = isinstance(items, list) and len(items) > 0
    verdict = "SKIP (has payment source = converting)" if has_card else "NURTURE-ELIGIBLE (no payment source)"
    print(json.dumps({
        "mem": m.get("id"), "patient": pid, "name": rname(nm),
        "plan": (m.get("plan") or {}).get("name"),
        "start_date": m.get("start_date"), "status": m.get("status"),
        "never_been_billed": m.get("never_been_billed"),
        "payment_methods_count": n, "verdict": verdict,
    }, default=str))
