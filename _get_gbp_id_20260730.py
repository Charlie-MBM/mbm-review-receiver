#!/usr/bin/env python3
"""Print the Hint catalog id for 'Google Business Profile' (created 2026-07-30).
Read-only; prints lead-source names+ids only (practice config, no patient data)."""
import os
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
base = os.environ.get("HINT_BASE_URL") or "https://api.hint.com"
r = requests.get(f"{base}/api/provider/lead_sources",
                 headers={"Authorization": f"Bearer {os.environ['HINT_API_KEY']}"}, timeout=30)
r.raise_for_status()
for e in r.json():
    name = e.get("name") if isinstance(e, dict) else None
    if name and "business profile" in str(name).lower():
        print(f"GBP catalog entry: name={name!r} id={e.get('id')!r}")
        break
else:
    print("NOT FOUND — catalog may not have refreshed yet; rerun in a minute.")
