#!/usr/bin/env python3
"""T5d read-only discovery: plans, patient search by email/phone, patient
archive/status fields, signup pages. No writes. PII redacted."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}
DUMMY = "pat-WwxwxOEdr9BI"


def g(path, **params):
    r = http.get(f"{BASE}{path}", headers=H, params=params, timeout=25)
    return r


# 1) PLANS
print("=== GET /api/provider/plans ===")
r = g("/api/provider/plans")
print("status", r.status_code)
if r.status_code == 200:
    d = r.json()
    plans = d if isinstance(d, list) else d.get("data", [])
    print("count:", len(plans))
    for p in plans:
        if isinstance(p, dict):
            print(f"  {p.get('id')}  name={p.get('name')!r}  type={p.get('plan_type')}  status={p.get('status')}")

# 2) PATIENT object: archive/status fields
print("\n=== dummy patient: status/archive-ish fields ===")
r = g(f"/api/provider/patients/{DUMMY}")
if r.status_code == 200:
    p = r.json()
    for k in p.keys():
        if any(t in k.lower() for t in ("status", "archiv", "active", "deactiv", "deleted", "state")):
            print(f"  {k} = {p.get(k)!r}")
    print("  (email present:", bool(p.get("email")), "| phones:", len(p.get("phones") or []), ")")

# 3) PATIENT SEARCH by email / phone / query
print("\n=== patient search mechanics ===")
# grab a real active patient's email+phone to search for
allp = g("/api/provider/patients", limit=5)
sample_email = sample_phone = None
if allp.status_code == 200:
    d = allp.json()
    pts = d if isinstance(d, list) else d.get("data", [])
    for pt in pts:
        if pt.get("email"):
            sample_email = pt["email"]
        ph = pt.get("phones") or []
        if ph and ph[0].get("number"):
            sample_phone = ph[0]["number"]
        if sample_email and sample_phone:
            break
for qp in (("email", sample_email), ("phone", sample_phone), ("q", sample_email), ("search", sample_email)):
    if not qp[1]:
        continue
    r = g("/api/provider/patients", **{qp[0]: qp[1]})
    d = r.json() if r.status_code == 200 else None
    n = (len(d) if isinstance(d, list) else len(d.get("data", []))) if d is not None else "?"
    print(f"  ?{qp[0]}=<sample> -> {r.status_code}, results={n}")

# total patients (for full-scan feasibility)
r = g("/api/provider/patients")
if r.status_code == 200:
    d = r.json()
    pts = d if isinstance(d, list) else d.get("data", [])
    print("  unfiltered /patients returned:", len(pts), "| has 'data' wrapper:", isinstance(r.json(), dict))
    # pagination hints
    if isinstance(r.json(), dict):
        print("  top-level keys:", list(r.json().keys()))

# 4) SIGNUP PAGES via API?
print("\n=== signup pages via API? ===")
for path in ["/api/provider/signup_pages", "/api/provider/online_signup", "/api/provider/signups", "/api/provider/enrollment_pages"]:
    r = g(path)
    print(f"  {r.status_code}  {path}")
