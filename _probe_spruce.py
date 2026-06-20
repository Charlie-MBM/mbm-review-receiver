#!/usr/bin/env python3
"""Read-only probe of the Spruce API to learn contact/tag shapes for the
nurture poller build. Prints PII-redacted structure only. Safe: no writes."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["SPRUCE_API_KEY"]
BASE = "https://api.sprucehealth.com/v1"
H = {"Authorization": f"Bearer {KEY}"}


def redact_name(s):
    s = (s or "").strip()
    if not s:
        return "(none)"
    parts = s.split()
    out = parts[0]
    if len(parts) > 1:
        out += " " + parts[-1][0] + "."
    return out


print("=== GET /v1/contacts/tags ===")
r = http.get(f"{BASE}/contacts/tags", headers=H, timeout=30)
print("status", r.status_code)
tagdata = r.json()
print("top-level keys:", list(tagdata.keys()) if isinstance(tagdata, dict) else type(tagdata))
tags = tagdata.get("contactTags") or tagdata.get("tags") or tagdata.get("data") or tagdata
if isinstance(tags, list):
    for t in tags:
        if isinstance(t, dict):
            print("  tag:", json.dumps({k: t.get(k) for k in t.keys()}))
print()

print("=== GET /v1/contacts (first page) ===")
r = http.get(f"{BASE}/contacts", headers=H, params={"pageSize": 200}, timeout=30)
print("status", r.status_code)
data = r.json()
print("top-level keys:", list(data.keys()) if isinstance(data, dict) else type(data))
contacts = data.get("contacts") or data.get("data") or []
print("hasMore:", data.get("hasMore"))
print("count this page:", len(contacts))
if contacts:
    sample = contacts[0]
    print("\n--- one contact object: full key list ---")
    print(list(sample.keys()))
    print("\n--- one contact object: redacted JSON ---")
    red = {}
    for k, v in sample.items():
        if k.lower() in ("displayname", "name", "firstname", "lastname"):
            red[k] = redact_name(str(v))
        elif "phone" in k.lower() or "email" in k.lower() or k.lower() in ("phonenumbers", "emailaddresses", "endpoints"):
            red[k] = f"<{type(v).__name__} present>"
        else:
            red[k] = v
    print(json.dumps(red, indent=2, default=str)[:2500])

# Tally tags across all pages
print("\n=== tag tally across ALL contacts ===")
from collections import Counter
tag_counter = Counter()
nurture = []
total = 0
token = None
pages = 0
while True:
    params = {"pageSize": 200}
    if token:
        params["paginationToken"] = token
    r = http.get(f"{BASE}/contacts", headers=H, params=params, timeout=30)
    d = r.json()
    cs = d.get("contacts") or d.get("data") or []
    for c in cs:
        total += 1
        ctags = c.get("tags") or c.get("contactTags") or []
        names = []
        for ct in ctags:
            nm = ct.get("name") if isinstance(ct, dict) else ct
            names.append(nm)
            tag_counter[nm] += 1
        if any((n or "").lower() == "nurture-prospect" for n in names):
            has_phone = False
            for fld in ("phoneNumbers", "phones", "endpoints"):
                v = c.get(fld)
                if v:
                    has_phone = True
            nurture.append({
                "id": c.get("id"),
                "name": redact_name(c.get("displayName") or c.get("name") or ""),
                "type": c.get("type") or c.get("contactType"),
                "tags": names,
                "has_phone_field": has_phone,
            })
    pages += 1
    if d.get("hasMore") and d.get("paginationToken"):
        token = d["paginationToken"]
    else:
        break
print("total contacts:", total, "pages:", pages)
print("tag tally:", json.dumps(tag_counter.most_common(), default=str))
print("\n=== contacts tagged nurture-prospect ===")
print("count:", len(nurture))
for n in nurture:
    print(json.dumps(n, default=str))
