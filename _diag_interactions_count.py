"""
Diagnostic ONLY. Probes the Hint interactions endpoint to find the real total,
the pagination scheme, and the object schema. Prints ONLY counts and field
NAMES (no patient values), so output is safe to share.
Run:  py _diag_interactions_count.py
"""
import os
import requests

BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
KEY = os.environ["HINT_API_KEY"]
URL = f"{BASE}/api/provider/interactions"
H = {"Authorization": f"Bearer {KEY}"}
SINCE = "2026-06-09T00:00:00+00:00"


def probe(params, label):
    try:
        r = requests.get(URL, headers=H, params=params, timeout=30)
        d = r.json()
        if isinstance(d, list):
            items, shape = d, "list"
        else:
            items, shape = d.get("data", []), "dict:" + ",".join(k for k in d if k != "data")
        pids = {i.get("patient_id") for i in items if isinstance(i, dict) and i.get("patient_id")}
        print(f"{label:32} HTTP {r.status_code}  items={len(items):<4} distinct_patients={len(pids):<4} shape={shape}")
        return items
    except Exception as e:
        print(f"{label:32} ERROR {e}")
        return []


base = probe({"created_at_after": SINCE}, "since-Jun9 (poller default)")
probe({}, "no filter, default")
probe({"created_at_after": SINCE, "limit": 200}, "since-Jun9 limit=200")
probe({"created_at_after": SINCE, "per_page": 200}, "since-Jun9 per_page=200")
probe({"created_at_after": SINCE, "page": 2}, "since-Jun9 page=2")
probe({"limit": 200}, "no filter limit=200")

# schema: FIELD NAMES only of one interaction (no values -> no PHI)
if base and isinstance(base[0], dict):
    print("\ninteraction object field names:", sorted(base[0].keys()))
