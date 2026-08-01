#!/usr/bin/env python3
"""
_probe_hint_revenue.py -- READ ONLY. Find where Hint exposes COLLECTED revenue so
export_dashboard_members.py can wire the dashboard profitability widget's revenue side.

The exporter currently guesses four practice-wide list endpoints
(/api/provider/payments|invoices|transactions|charges) and all return nothing, so
revenue_mtd stays null. This probe tests, in one run:
  (1) practice-wide LIST endpoints (payments/invoices/transactions/charges/billing)
      -> HTTP status, row count, and item FIELD NAMES only
  (2) patient-scoped SUBRESOURCES on Charlie's own record
      (/patients/{ME}/invoices|payments|charges|transactions|ledger)
  (3) query-param scoping (/invoices?patient_id=ME, ?patient=ME, ?status=paid)
  (4) billing fields embedded on the patient object itself

PHI-SAFE BY CONSTRUCTION:
  - Patient-scoped calls use ONLY Charlie's authorized record (pat-z7Pu6cu2FtQg).
  - Practice-wide calls print status + count + FIELD NAMES ONLY. Never a patient
    value, name, amount, memo, or date. Safe to paste the whole output back.
  - No writes anywhere.

Run on the laptop (where HINT_API_KEY lives):  py _probe_hint_revenue.py
Then paste the output back into the chat.
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import requests as http

KEY  = os.environ["HINT_API_KEY"]
BASE = os.environ.get("HINT_BASE_URL", "https://api.hint.com").rstrip("/")
H    = {"Authorization": f"Bearer {KEY}"}
ME   = "pat-z7Pu6cu2FtQg"   # Charlie's own authorized test record

AMOUNTISH = ("amount", "total", "cents", "paid", "balance", "price", "value", "due", "sum")
DATEISH   = ("paid_at", "collected_at", "created_at", "date", "charged_at", "posted_at", "settled")
STATUSISH = ("status", "state", "paid")

def get(path, params=None):
    try:
        r = http.get(f"{BASE}/api/provider/{path.lstrip('/')}", headers=H, params=params or {}, timeout=30)
        return r.status_code, r
    except Exception as e:
        return None, str(e)

def rows_of(r):
    try:
        d = r.json()
    except Exception:
        return None, None
    if isinstance(d, list):
        return d, None
    if isinstance(d, dict):
        for k in ("data", "results", "items", "records", "invoices", "payments", "charges", "transactions"):
            if isinstance(d.get(k), list):
                return d[k], k
        return None, "(dict, no obvious list key: keys=%s)" % sorted(d.keys())
    return None, None

def flag(names):
    """annotate which field names look like amount / date / status columns"""
    out = []
    for n in names:
        low = n.lower()
        tags = []
        if any(w in low for w in AMOUNTISH):  tags.append("$")
        if any(w in low for w in DATEISH):     tags.append("date")
        if any(w in low for w in STATUSISH):   tags.append("status")
        out.append(n + (("  <-- " + ",".join(tags)) if tags else ""))
    return out

print(f"BASE={BASE}\n")

print("=== (1) practice-wide LIST endpoints (status + count + field NAMES only) ===")
for ep in ["payments", "invoices", "transactions", "charges", "billing", "customer_invoices", "ledger_entries"]:
    code, r = get(ep, {"limit": 5})
    if code != 200:
        print(f"  GET /{ep:20s} -> {code}")
        continue
    rows, note = rows_of(r)
    if rows is None:
        print(f"  GET /{ep:20s} -> 200  {note}")
    else:
        fields = sorted(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        print(f"  GET /{ep:20s} -> 200  rows>={len(rows)}")
        for f in flag(fields):
            print(f"        {f}")

print("\n=== (2) patient-scoped SUBRESOURCES on Charlie's own record ===")
for sub in ["invoices", "payments", "charges", "transactions", "ledger", "billing"]:
    code, r = get(f"patients/{ME}/{sub}", {"limit": 5})
    if code != 200:
        print(f"  GET /patients/ME/{sub:12s} -> {code}")
        continue
    rows, note = rows_of(r)
    if rows is None:
        print(f"  GET /patients/ME/{sub:12s} -> 200  {note}")
    else:
        fields = sorted(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        print(f"  GET /patients/ME/{sub:12s} -> 200  rows={len(rows)}")
        for f in flag(fields):
            print(f"        {f}")

print("\n=== (3) query-param scoping on /invoices (Charlie's record only) ===")
for params in [{"patient_id": ME, "limit": 5}, {"patient": ME, "limit": 5}, {"patient_id": ME, "status": "paid", "limit": 5}]:
    code, r = get("invoices", params)
    n = None
    if code == 200:
        rows, _ = rows_of(r); n = len(rows) if rows is not None else None
    print(f"  GET /invoices {json.dumps(params)} -> {code} rows={n}")

print("\n=== (4) billing fields embedded on the patient object (Charlie's record) ===")
code, r = get(f"patients/{ME}")
if code == 200:
    d = r.json()
    for k in sorted(d.keys()):
        low = k.lower()
        if any(w in low for w in ["charg", "invoic", "balanc", "bill", "payment", "transaction", "ledger", "revenue"]):
            v = d[k]
            t = type(v).__name__
            extra = f"({len(v)})" if isinstance(v, (list, dict)) else ""
            print(f"  >> {k}: {t}{extra}")
            if isinstance(v, list) and v and isinstance(v[0], dict):
                print("       item fields:", ", ".join(flag(sorted(v[0].keys()))))
            elif isinstance(v, dict):
                print("       fields:", ", ".join(flag(sorted(v.keys()))))
else:
    print("  GET patient object failed:", code)

print("\nDone (read-only). Safe to paste all output back.")
