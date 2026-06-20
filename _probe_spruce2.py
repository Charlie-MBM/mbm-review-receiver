#!/usr/bin/env python3
"""Probe v2: contact tag 'value' field, integrationLinks (Hint link),
phoneNumbers shape, HintStatus_Inactive candidates, locate ZZ-TEST dummy.
Read-only. PII redacted in output."""
import os, json
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["SPRUCE_API_KEY"]
BASE = "https://api.sprucehealth.com/v1"
H = {"Authorization": f"Bearer {KEY}"}


def rname(s):
    s = (s or "").strip()
    if not s:
        return "(none)"
    p = s.split()
    return p[0] + (" " + p[-1][0] + "." if len(p) > 1 else "")


def tag_values(c):
    out = []
    for t in (c.get("tags") or []):
        if isinstance(t, dict):
            out.append(t.get("value") or t.get("name"))
        else:
            out.append(t)
    return out


def phone_present(c):
    pn = c.get("phoneNumbers")
    if isinstance(pn, list):
        return len(pn) > 0
    return bool(pn)


# fetch all contacts
contacts, token = [], None
while True:
    params = {"pageSize": 200}
    if token:
        params["paginationToken"] = token
    d = http.get(f"{BASE}/contacts", headers=H, params=params, timeout=30).json()
    contacts += d.get("contacts", [])
    if d.get("hasMore") and d.get("paginationToken"):
        token = d["paginationToken"]
    else:
        break

print("total contacts:", len(contacts))
tc = Counter()
for c in contacts:
    for v in tag_values(c):
        tc[v] += 1
print("tag tally (by value):", json.dumps(tc.most_common(), default=str))

# show full shape of one Hint-synced contact (has integrationLinks)
synced = next((c for c in contacts if c.get("integrationLinks")), None)
print("\n--- a Hint-synced contact, redacted ---")
if synced:
    red = {}
    for k, v in synced.items():
        if k in ("displayName",):
            red[k] = rname(str(v))
        elif k in ("phoneNumbers", "emailAddresses"):
            red[k] = f"<{len(v) if isinstance(v,list) else v} item(s); sample keys: " + \
                     (str(list(v[0].keys())) if isinstance(v, list) and v and isinstance(v[0], dict) else 'n/a') + ">"
        elif k == "integrationLinks":
            red[k] = v  # want to see the Hint link shape
        elif k == "tags":
            red[k] = tag_values(synced)
        else:
            red[k] = v
    print(json.dumps(red, indent=2, default=str)[:3000])

# phoneNumbers detailed shape (redact actual number)
print("\n--- phoneNumbers element shape (one with a phone) ---")
withphone = next((c for c in contacts if phone_present(c)), None)
if withphone and isinstance(withphone.get("phoneNumbers"), list):
    el = withphone["phoneNumbers"][0]
    if isinstance(el, dict):
        shown = {k: ("<redacted>" if k.lower() in ("value", "number", "displayvalue") else v) for k, v in el.items()}
        print(json.dumps(shown, default=str))

# HintStatus_Inactive candidates (past-attendee upper bound), redacted
print("\n--- HintStatus_Inactive contacts (past-attendee candidate pool) ---")
inactive = [c for c in contacts if "HintStatus_Inactive" in tag_values(c)]
print("count:", len(inactive))
for c in inactive:
    print(json.dumps({
        "id": c.get("id"),
        "name": rname(c.get("displayName")),
        "category": c.get("category"),
        "has_phone": phone_present(c),
        "has_email": bool(c.get("emailAddresses")),
        "tags": tag_values(c),
        "integrationLinks": c.get("integrationLinks"),
    }, default=str))

# locate ZZ-TEST / NurtureQA dummy
print("\n--- ZZ-TEST / NurtureQA dummy ---")
for c in contacts:
    dn = (c.get("displayName") or "")
    if "ZZ" in dn.upper() or "NURTUREQA" in dn.upper().replace("-", "").replace(" ", ""):
        print(json.dumps({
            "id": c.get("id"), "displayName": dn, "category": c.get("category"),
            "has_phone": phone_present(c), "has_email": bool(c.get("emailAddresses")),
            "tags": tag_values(c), "canEdit": c.get("canEdit"), "canDelete": c.get("canDelete"),
        }, default=str))
