#!/usr/bin/env python3
"""Print the FIELD NAMES on a Hint patient record - never the values.

Needed to build the nightly Abstractive job: it has to pull date of birth, sex
and address out of Hint to search the HIE, and we need the exact field names.

PHI-safe by construction: prints key names, value types and string LENGTHS
only. Defaults to Charlie's OWN patient record (pat-z7Pu6cu2FtQg), which he
has authorised for testing - never another patient.

  py hint_patient_shape.py
  py hint_patient_shape.py <patient_id>
"""
import json
import sys
import urllib.request
from pathlib import Path

SELF_RECORD = "pat-z7Pu6cu2FtQg"   # Charlie's own record, authorised for testing


def load_env():
    env = {}
    p = Path(__file__).parent / ".env"
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def shape(node, depth=0, label="(root)"):
    pad = "  " * depth
    if isinstance(node, dict):
        print(f"{pad}{label}: dict[{len(node)}]")
        if depth >= 3:
            print(f"{pad}  ... keys: {sorted(node.keys())}")
            return
        for k in sorted(node.keys()):
            shape(node[k], depth + 1, k)
    elif isinstance(node, list):
        print(f"{pad}{label}: list[{len(node)}]")
        if node and depth < 3:
            shape(node[0], depth + 1, "[0]")
    elif isinstance(node, str):
        print(f"{pad}{label}: str(len={len(node)})")
    else:
        print(f"{pad}{label}: {type(node).__name__}")


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else SELF_RECORD
    env = load_env()
    key = env.get("HINT_API_KEY", "")
    if not key:
        sys.exit("HINT_API_KEY not set in .env")
    base = ("https://api.hint.com" if env.get("HINT_ENV") == "production"
            else "https://api.sandbox.hint.com")
    req = urllib.request.Request(
        f"{base}/api/provider/patients/{pid}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        doc = json.load(r)
    print(f"GET /api/provider/patients/{pid} -> 200")
    print("-" * 60)
    shape(doc)
    print("-" * 60)
    print("Field names only - no values printed.")


if __name__ == "__main__":
    main()
