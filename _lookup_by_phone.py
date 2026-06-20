"""Find a Hint patient by phone number.

Usage:
  py _lookup_by_phone.py 3603498094

Strips formatting and searches the patient list. Prints {patient_id, first_name}
for any match (redacts everything else).
"""
import os
import re
import sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

import requests

if len(sys.argv) < 2:
    print("usage: py _lookup_by_phone.py <phone>")
    sys.exit(1)

target_raw = sys.argv[1]
target = re.sub(r"\D", "", target_raw)  # digits only
print(f"searching for phone digits: ...{target[-4:]}")

base = os.environ.get("HINT_BASE_URL") or "https://api.hint.com"
key = os.environ["HINT_API_KEY"]
url = f"{base}/api/provider/patients"
headers = {"Authorization": f"Bearer {key}"}

# Page through all patients (Hint paginates ~50/page typically).
page = 1
found = []
while True:
    resp = requests.get(url, headers=headers, params={"page": page, "per_page": 100}, timeout=30)
    if resp.status_code != 200:
        print(f"error: HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(2)
    data = resp.json()
    patients = data if isinstance(data, list) else data.get("data", [])
    if not patients:
        break
    for p in patients:
        phones = p.get("phones") or []
        for ph in phones:
            if not isinstance(ph, dict):
                continue
            num = re.sub(r"\D", "", str(ph.get("number") or ""))
            # match last 10 digits to handle country-code variants
            if num and (num.endswith(target) or target.endswith(num[-10:])):
                found.append({
                    "patient_id": p.get("id") or p.get("patient_id") or "?",
                    "first_name": p.get("first_name"),
                    "phone_type": ph.get("type"),
                    "phone_last4": num[-4:] if num else "",
                    "email_partial": (p.get("email") or "")[:3] + "***" if p.get("email") else "",
                })
                break
    print(f"  scanned page {page} ({len(patients)} patients) — running matches: {len(found)}")
    if len(patients) < 100:
        break
    page += 1
    if page > 50:
        print("  hit page limit (50), stopping")
        break

print(f"\n=== {len(found)} match(es) ===")
for m in found:
    print(m)
