"""
Diagnostic ONLY. Reads CHARLIE'S OWN authorized test record (pat-z7Pu6cu2FtQg)
to discover the membership date field names. Stays on this single record; prints
membership-related fields of Charlie's own record only. Run:
  py _diag_patient_membership.py
"""
import os
import json
import requests

BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
KEY = os.environ["HINT_API_KEY"]
PID = "pat-z7Pu6cu2FtQg"  # Charlie's own record, authorized for testing

r = requests.get(f"{BASE}/api/provider/patients/{PID}", headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
r.raise_for_status()
p = r.json()

print("patient top-level keys:", sorted(p.keys()))
print("\nmembership / date-ish fields (Charlie's own record):")
for k in sorted(p.keys()):
    lk = k.lower()
    if "member" in lk or "enroll" in lk or "active" in lk or "status" in lk or lk.endswith("_at") or "date" in lk or "since" in lk:
        v = p[k]
        if isinstance(v, (dict, list)):
            print(f"  {k}: {json.dumps(v)[:400]}")
        else:
            print(f"  {k}: {v}")
