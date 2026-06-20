"""Quick debug: print all top-level fields Hint returns for one patient.

Usage:
  py _debug_patient.py pat-28xvYLdRMBaQ

(Pick any patient_id from the dry-run output — Mark's was pat-28xvYLdRMBaQ.)
"""
import sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

from hint_webhook_receiver import fetch_patient

if len(sys.argv) < 2:
    print("usage: py _debug_patient.py <patient_id>")
    sys.exit(1)

pid = sys.argv[1]
p = fetch_patient(pid)
if not p:
    print(f"no patient returned for {pid}")
    sys.exit(2)

print(f"=== patient {pid} ===")
print(f"top-level keys: {sorted(p.keys())}")
print()
# Print any field that looks phone/contact related, value redacted to last 4 digits
PHONE_HINTS = ("phone", "mobile", "cell", "contact", "tel")
for k in sorted(p.keys()):
    if any(h in k.lower() for h in PHONE_HINTS):
        v = p[k]
        if isinstance(v, str) and v:
            print(f"  {k!r}: ...{v[-4:]}  (length {len(v)})")
        elif isinstance(v, list):
            print(f"  {k!r}: list with {len(v)} items, first item keys: {sorted(v[0].keys()) if v and isinstance(v[0], dict) else '<not dict>'}")
        elif isinstance(v, dict):
            print(f"  {k!r}: dict with keys {sorted(v.keys())}")
        else:
            print(f"  {k!r}: {type(v).__name__} = {v!r}")
    elif k in ("first_name", "chosen_first_name", "email"):
        v = p[k]
        if isinstance(v, str):
            # redact email partially
            if k == "email" and "@" in v:
                local, domain = v.split("@", 1)
                print(f"  {k!r}: {local[:2]}***@{domain}")
            else:
                print(f"  {k!r}: {v!r}")
