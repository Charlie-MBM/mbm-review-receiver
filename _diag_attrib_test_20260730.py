#!/usr/bin/env python3
"""Verify the 2026-07-30 lead-attribution deploy end-to-end. READ-ONLY, PHI-safe.

The synthetic lead: name "Testgamma Synthetic", phone (360) 349-8094, submitted
via /concierge-primary-care?gclid=CLAUDETEST20260730a at ~10:15 PT 2026-07-30.

Prints attribution fields ONLY for patients whose first name starts with "Test"
(case-insensitive). Any other match is counted and redacted, never printed.

Verdict guide:
  lead_source = "Google Ads"            -> WORKING (client sent gclid, Worker derived channel)
  lead_source = "Other" (+ lead_source_other "Lead magnet (...)") -> some old code path served
  matched an existing Test patient whose lead_source predates today -> INCONCLUSIVE
     (dedupe-by-phone PATCHes set-if-empty; rerun with a phone no test patient has used)

Run:  py _diag_attrib_test_20260730.py
"""
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    print("ABORT: python-dotenv not installed.", file=sys.stderr)
    raise SystemExit(2)

import requests

TARGET = "3603498094"

base = os.environ.get("HINT_BASE_URL") or "https://api.hint.com"
key = os.environ["HINT_API_KEY"]
url = f"{base}/api/provider/patients"
headers = {"Authorization": f"Bearer {key}"}

page, matches, redacted = 1, [], 0
while True:
    resp = requests.get(url, headers=headers, params={"page": page, "per_page": 100}, timeout=30)
    if resp.status_code != 200:
        print(f"error: HTTP {resp.status_code}: {resp.text[:200]}")
        raise SystemExit(2)
    data = resp.json()
    patients = data if isinstance(data, list) else data.get("data", [])
    if not patients:
        break
    for p in patients:
        first = str(p.get("first_name") or "")
        is_test = first.lower().startswith("test")
        hit_phone = False
        for ph in p.get("phones") or []:
            if isinstance(ph, dict):
                num = re.sub(r"\D", "", str(ph.get("number") or ""))
                if num and num.endswith(TARGET):
                    hit_phone = True
                    break
        if not (hit_phone or is_test):
            continue
        if not is_test:
            redacted += 1  # non-test patient on this phone: count, never print
            continue
        ls = p.get("lead_source")
        if isinstance(ls, dict):
            ls_repr = {k: ls.get(k) for k in ("id", "name", "value", "label") if k in ls}
        else:
            ls_repr = ls
        matches.append({
            "first_name": first,
            "phone_hit": hit_phone,
            "lead_source": ls_repr,
            "lead_source_other": p.get("lead_source_other"),
            "created_at": p.get("created_at"),
            "patient_id": p.get("id") or "?",
        })
    if len(patients) < 100:
        break
    page += 1
    if page > 50:
        print("hit page limit (50), stopping")
        break

print(f"=== test-prefixed matches: {len(matches)} | non-test matches on that phone (REDACTED): {redacted} ===")
for m in matches:
    print(m)
if not matches and not redacted:
    print("NO patient found on that phone at all -> the Hint patient-create side-effect did not run; check Worker logs.")
