#!/usr/bin/env python3
"""Dump Hint's lead-source catalog as label -> id. PHI-FREE.

This endpoint returns PRACTICE CONFIGURATION only -- the list of lead-source
options and their ids. No patient names, no patient ids, nothing identifying.
Safe to paste the output back into chat.

Needed because two new catalog entries were created in the Hint UI on
2026-07-30 ("Google Ads" and "Google Local Services") and their lds- ids are
not exposed anywhere in the admin page's DOM. Both the Worker (src/book/hint.ts)
and the poller carry a HARDCODED FALLBACK SNAPSHOT of these ids, used whenever
the live catalog fetch fails. Until the snapshot has the two new ids, a
fallback-path write for either label will miss and silently degrade to
"Other" + lead_source_other -- which is precisely the failure mode we just
spent this work fixing.

Run from mbm-review-receiver:  py _diag_lead_sources_20260730.py
"""
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except Exception:
    pass

import requests

KEY = os.environ.get("HINT_API_KEY", "")
ENV = os.environ.get("HINT_ENV", "production")
BASE = "https://api.hint.com" if ENV == "production" else "https://api.hint-staging.com"

if not KEY:
    print("ERROR: HINT_API_KEY not set (check .env)", file=sys.stderr)
    raise SystemExit(2)

r = requests.get(f"{BASE}/api/provider/lead_sources",
                 headers={"Authorization": f"Bearer {KEY}"}, timeout=40)
print(f"HTTP {r.status_code}  {BASE}/api/provider/lead_sources\n")
if r.status_code != 200:
    print(r.text[:800], file=sys.stderr)
    raise SystemExit(3)

data = r.json()
rows = data if isinstance(data, list) else (data.get("lead_sources") or data.get("data") or [])

print(f"{len(rows)} lead sources\n")
print(f"{'label':<34} {'id':<22} show_on_signup")
print("-" * 74)
for x in sorted(rows, key=lambda d: (d.get("name") or "").lower()):
    name = x.get("name") or x.get("label") or "?"
    lid = x.get("id") or "?"
    show = x.get("show_on_online_signup")
    if show is None:
        show = x.get("online_signup")
    print(f"{name:<34} {lid:<22} {show}")

print("\nPaste the whole table back to Claude -- it is configuration, not PHI.")
