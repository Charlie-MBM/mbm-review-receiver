#!/usr/bin/env python3
"""Gate 6: is there a per-patient completion/access/payment-request link the
poller can reference? GET-only discovery (no POSTs, no sends). Also scan the
membership + patient objects for any hosted URL field. Read-only/safe."""
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests as http

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
KEY = os.environ["HINT_API_KEY"]
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
H = {"Authorization": f"Bearer {KEY}"}
DUMMY = "pat-WwxwxOEdr9BI"
MEM = "mem-NomErLrkhCXp"

# 1) scan membership + patient for any URL-ish field
print("=== scan membership object for URL/link fields ===")
m = http.get(f"{BASE}/api/provider/memberships/{MEM}", headers=H, timeout=20).json()
def find_urls(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and ("http" in v.lower() or "link" in k.lower() or "url" in k.lower() or "web" in k.lower()):
                out.append((path + "/" + k, v[:80]))
            out += find_urls(v, path + "/" + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            out += find_urls(v, f"{path}[{i}]")
    return out
for p, v in find_urls(m):
    print("  ", p, "=", v)
print("  upcoming_bills:", json.dumps(m.get("upcoming_bills"), default=str)[:300])

p = http.get(f"{BASE}/api/provider/patients/{DUMMY}", headers=H, timeout=20).json()
print("=== scan patient object for URL/link fields ===")
for pth, v in find_urls(p):
    print("  ", pth, "=", v)
print("  provider_web_link:", p.get("provider_web_link"))

# 2) GET-probe candidate completion endpoints (GET on POST-only routes returns
#    404/405 WITHOUT side effects; 200 means a readable resource exists)
print("\n=== GET-probe candidate link/payment endpoints (no sends) ===")
candidates = [
    f"/api/provider/patients/{DUMMY}/access_link",
    f"/api/provider/patients/{DUMMY}/access_links",
    f"/api/provider/patients/{DUMMY}/invite",
    f"/api/provider/patients/{DUMMY}/invitation",
    f"/api/provider/patients/{DUMMY}/payment_request",
    f"/api/provider/patients/{DUMMY}/payment_requests",
    f"/api/provider/patients/{DUMMY}/request_payment_info",
    f"/api/provider/memberships/{MEM}/access_link",
    f"/api/provider/memberships/{MEM}/payment_request",
    f"/api/provider/memberships/{MEM}/invoice",
    f"/api/provider/patients/{DUMMY}/portal",
    f"/api/provider/patients/{DUMMY}/portal_link",
    f"/api/provider/patients/{DUMMY}/checkout",
    f"/api/provider/patients/{DUMMY}/billing_link",
]
for path in candidates:
    try:
        r = http.get(f"{BASE}{path}", headers=H, timeout=12)
        body = "" if r.status_code == 404 else r.text[:120].replace("\n", " ")
        print(f"  {r.status_code}  {path}  {body}")
    except Exception as e:
        print(f"  ERR  {path}  {e}")
