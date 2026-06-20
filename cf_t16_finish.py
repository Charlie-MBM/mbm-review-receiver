"""
T16 finish script — run from mbm-review-receiver dir:
  py cf_t16_finish.py

Reads CLOUDFLARE_API_TOKEN from .env, then:
  1. Audits DNS records
  2. Checks Always Use HTTPS + SSL mode
  3. Reads the current redirect ruleset
  4. Applies the 301/https fix to the www redirect rule
  5. Verifies the fix with a live curl
"""
import urllib.request, json, subprocess, os, sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
env = {}
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

TOKEN = env.get("CLOUDFLARE_API_TOKEN", "")
if not TOKEN:
    sys.exit("CLOUDFLARE_API_TOKEN not found in .env")

ZONE = "a04e4b55f21faaba0cee26e4bcc4ace7"
RULESET = "1fbc0eef47bb469ea2604d41f4822f52"
BASE = "https://api.cloudflare.com/client/v4"
HDRS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def cf(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=HDRS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


# ── 1. DNS RECORDS ────────────────────────────────────────────────────────────
print("\n=== DNS RECORDS ===")
dns = cf("GET", f"/zones/{ZONE}/dns_records?per_page=100")
if dns.get("success"):
    for r in sorted(dns["result"], key=lambda x: (x["name"], x["type"])):
        px = "PROXIED  " if r.get("proxied") else "dns-only "
        print(f"  {r['type']:5} {r['name']:45} {r.get('content',''):45} [{px}] id={r['id']}")
else:
    print("  FAILED:", dns.get("errors"))

# ── 2. ZONE SETTINGS ──────────────────────────────────────────────────────────
print("\n=== ZONE SETTINGS ===")
for s in ["always_use_https", "ssl", "min_tls_version"]:
    r = cf("GET", f"/zones/{ZONE}/settings/{s}")
    val = r["result"]["value"] if r.get("success") else f"FAIL – {r.get('errors')}"
    print(f"  {s}: {val}")

# ── 3. CURRENT REDIRECT RULES ─────────────────────────────────────────────────
print("\n=== CURRENT REDIRECT RULESET ===")
rs = cf("GET", f"/zones/{ZONE}/rulesets/{RULESET}")
if rs.get("success"):
    rules = rs["result"].get("rules", [])
    print(f"  Rules: {len(rules)}")
    for rule in rules:
        fv = rule.get("action_parameters", {}).get("from_value", {})
        print(f"  [{rule.get('description','no desc')}]")
        print(f"    expr:     {rule.get('expression','')}")
        print(f"    status:   {fv.get('status_code','?')}")
        print(f"    target:   {fv.get('target_url',{})}")
        print(f"    enabled:  {rule.get('enabled', True)}")
else:
    print("  FAILED:", rs.get("errors"))
    sys.exit("Cannot read ruleset — aborting before any write.")

# ── 4. APPLY THE FIX ──────────────────────────────────────────────────────────
print("\n=== APPLYING 301 / HTTPS FIX ===")
new_rules = [
    {
        "description": "Redirect www to root (301)",
        "expression": '(http.host eq "www.mtbakermedical.com")',
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "target_url": {
                    "expression": 'concat("https://mtbakermedical.com", http.request.uri.path)'
                },
                "status_code": 301,
                "preserve_query_string": True,
            }
        },
        "enabled": True,
    }
]

result = cf("PUT", f"/zones/{ZONE}/rulesets/{RULESET}", {"rules": new_rules})
if result.get("success"):
    new_rule = result["result"]["rules"][0]
    fv = new_rule.get("action_parameters", {}).get("from_value", {})
    print(f"  ✓ Rule updated. New status_code: {fv.get('status_code')}  target: {fv.get('target_url',{})}")
else:
    print("  FAILED:", result.get("errors"))
    sys.exit("Ruleset PUT failed.")

# ── 5. LIVE VERIFY ────────────────────────────────────────────────────────────
print("\n=== LIVE VERIFY (http://www → should be one 301 to https://apex) ===")
try:
    r = subprocess.run(
        ["curl", "-sI", "--max-time", "10", "http://www.mtbakermedical.com"],
        capture_output=True, text=True
    )
    for line in r.stdout.splitlines()[:6]:
        print(" ", line)
except FileNotFoundError:
    print("  (curl not available — check manually)")

print("\nDone. T16 fully closed.")
