#!/usr/bin/env python3
"""
_verify_revenue.py -- READ ONLY. Independently verify the dashboard's July revenue
number ($7,275 from export_dashboard_members.py) and catch a cents/dollars error.

Runs on the laptop only (Hint keys stay local). PHI-SAFE: prints ONLY aggregate
dollar stats (count, sum, avg, min, max, and an anonymized amount distribution).
NO patient names, memos, sources, or ids are printed or stored. Two independent
computations that should agree:
  A) sum of /api/provider/payments  amount_in_cents  (paid-like, dated this month)
  B) sum of /api/provider/customer_invoices  paid_in_cents  (paid_at this month)

Run:  py _verify_revenue.py
"""
import os, datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}
NOW  = datetime.date.today()
Y, M = NOW.year, NOW.month
PAIDISH = ("paid", "succeeded", "settled", "completed", "captured", "collected")

def get_all(path, params=None):
    out, off = [], 0
    while True:
        r = http.get(f"{BASE}{path}", headers=H, params={**(params or {}), "limit": 100, "offset": off}, timeout=40)
        r.raise_for_status()
        d = r.json()
        batch = d if isinstance(d, list) else (d.get("data") or d.get("payments") or d.get("invoices") or [])
        out += batch
        tot = r.headers.get("x-total-count")
        if len(batch) < 100 or (tot and len(out) >= int(tot)) or off > 40000:
            break
        off += 100
    return out

def in_month(s):
    if not s: return False
    try:
        return int(str(s)[:4]) == Y and int(str(s)[5:7]) == M
    except Exception:
        return False

def stats(cents_list, label):
    if not cents_list:
        print(f"  {label}: 0 rows"); return
    d = sorted(c/100.0 for c in cents_list)
    n = len(d); tot = sum(d)
    print(f"  {label}: n={n}  sum=${tot:,.2f}  avg=${tot/n:,.2f}  min=${d[0]:,.2f}  max=${d[-1]:,.2f}")
    # anonymized distribution (amounts only, no patient linkage) to eyeball a units error
    buckets = {"<$50":0,"$50-199":0,"$200-499":0,"$500-999":0,"$1k-4.9k":0,">=$5k":0}
    for v in d:
        buckets["<$50" if v<50 else "$50-199" if v<200 else "$200-499" if v<500 else
                "$500-999" if v<1000 else "$1k-4.9k" if v<5000 else ">=$5k"] += 1
    print("      distribution:", ", ".join(f"{k}:{v}" for k,v in buckets.items() if v))

print(f"Verifying collected revenue for {Y}-{M:02d}  (BASE={BASE})\n")

# A) payments endpoint
pay = get_all("/api/provider/payments")
a_cents = []
for p in pay:
    if not isinstance(p, dict): continue
    st = str(p.get("status") or "").lower()
    if st and not any(k in st for k in PAIDISH): continue
    if not in_month(p.get("date") or p.get("paid_at") or p.get("created_at")): continue
    v = p.get("amount_in_cents")
    if v is not None: a_cents.append(float(v))
print("A) /api/provider/payments (amount_in_cents, paid-like, this month):")
stats(a_cents, "payments")

# B) customer_invoices endpoint (paid portion)
inv = get_all("/api/provider/customer_invoices")
b_cents = []
for iv in inv:
    if not isinstance(iv, dict): continue
    if not in_month(iv.get("paid_at")): continue
    v = iv.get("paid_in_cents")
    if v: b_cents.append(float(v))
print("\nB) /api/provider/customer_invoices (paid_in_cents, paid this month):")
stats(b_cents, "invoices")

print("\nDashboard baked value: $7,275.00 (23 payments). A) above should match it.")
print("If max is a few hundred $, cents->dollars is correct. If max is ~$30k+, it's a units bug.")
print("Done (read-only, aggregates only).")
