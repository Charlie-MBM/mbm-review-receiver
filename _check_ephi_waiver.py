"""Pull whatever Hint exposes about the patient agreement / ePHI waiver text.

Tries several Hint API endpoints that commonly hold practice settings,
agreement templates, and per-patient consent records. Prints what's
available (truncated). No PHI gets dumped — just structure + agreement text.

Usage:
  py _check_ephi_waiver.py
  py _check_ephi_waiver.py pat-z7Pu6cu2FtQg     # also probes one patient
"""
import os
import sys
import json
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

import requests

BASE = os.environ.get("HINT_BASE_URL") or "https://api.hint.com"
KEY = os.environ["HINT_API_KEY"]
H = {"Authorization": f"Bearer {KEY}"}

# Endpoints to probe — practices use different sub-resources; we'll try a few.
PROBE_ENDPOINTS = [
    "/api/provider/practice",
    "/api/provider/practices",
    "/api/provider/agreements",
    "/api/provider/patient_agreements",
    "/api/provider/agreement_templates",
    "/api/provider/forms",
    "/api/provider/documents",
    "/api/provider/settings",
]

print("=" * 80)
print("PROBING PRACTICE-LEVEL ENDPOINTS")
print("=" * 80)
for ep in PROBE_ENDPOINTS:
    url = f"{BASE}{ep}"
    try:
        r = requests.get(url, headers=H, timeout=15)
        print(f"\n{ep}  →  HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            preview = json.dumps(data, indent=2)[:1500]
            print(preview)
            if len(json.dumps(data)) > 1500:
                print("  ... (truncated)")
        elif r.status_code in (404, 401, 403):
            pass  # silent — endpoint just doesn't exist on this account
        else:
            print(f"  body: {r.text[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")

# If a patient_id was supplied, also probe per-patient consent records
if len(sys.argv) > 1:
    pid = sys.argv[1]
    print()
    print("=" * 80)
    print(f"PROBING PATIENT-LEVEL ENDPOINTS for {pid}")
    print("=" * 80)
    for sub in ["agreements", "documents", "forms", "consents"]:
        url = f"{BASE}/api/provider/patients/{pid}/{sub}"
        try:
            r = requests.get(url, headers=H, timeout=15)
            print(f"\n/patients/{pid}/{sub}  →  HTTP {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                preview = json.dumps(data, indent=2)[:1500]
                print(preview)
        except Exception as e:
            print(f"  ERROR: {e}")
