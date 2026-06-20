## Cloudflare zone management via API (no dashboard needed)

This section documents API-based operations for `mtbakermedical.com`, established in T16 (2026-06-12). Use these patterns for all future DNS, redirect rule, and cache purge work — the goal is zero dashboard visits.

### Quick reference — mtbakermedical.com

| Key | Value |
|---|---|
| Zone ID | `a04e4b55f21faaba0cee26e4bcc4ace7` |
| Account ID | `6c7588442fd309cd501bea1a56dc3774` |
| Token env var | `CLOUDFLARE_ZONE_MGMT_TOKEN` (see scope note below) |
| Base URL | `https://api.cloudflare.com/client/v4` |
| Auth header | `Authorization: Bearer $TOKEN` |

Zone ID is not a secret — safe to reference in docs and scripts.

### Token scope note (important)

The token in `mbm-review-receiver/.env` as `CLOUDFLARE_API_TOKEN` has **Zone:Read only** as of T16. It was created for the review-receiver service and can list zones and rulesets but cannot edit DNS, zone settings, or redirect rules.

For zone management, create a properly-scoped token: CF Dashboard → **My Profile → API Tokens → Create Token → Custom Token**, zone scope `mtbakermedical.com`, with permissions:
- Zone: Read
- DNS Records: Edit
- Dynamic URL Redirects: Edit
- Zone Settings: Edit
- Cache Purge

Store it as `CLOUDFLARE_ZONE_MGMT_TOKEN` — don't overwrite the review-receiver token.

**Token verify endpoint quirk**: `GET /user/tokens/verify` returns "Invalid API Token" for zone-scoped tokens. This is expected — that endpoint requires `User.API Tokens: Read` (a user-level permission not included in zone-scoped tokens). If zone listing works, the token is valid.

### Auth pattern (Python, used in T16)

```python
import urllib.request, json

def cf_get(path, token):
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)

def cf_put(path, token, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT"
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)
```

### Operation 1: DNS record edit

```bash
ZONE_ID="a04e4b55f21faaba0cee26e4bcc4ace7"
TOKEN="$CLOUDFLARE_ZONE_MGMT_TOKEN"

# List all DNS records (get record IDs)
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?per_page=100" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Update a specific record
curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/RECORD_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "new-value", "proxied": true}'
```

Key fields: `type` (A/CNAME/MX/TXT), `name` (hostname), `content` (IP or target), `proxied` (`true` = orange cloud / CF proxy, `false` = gray cloud / DNS only).

### Operation 2: Redirect rule change

Redirect rules live in the `http_request_dynamic_redirect` ruleset. The ruleset ID for mtbakermedical.com is `1fbc0eef47bb469ea2604d41f4822f52` (confirmed T16, stable unless manually deleted).

```bash
ZONE_ID="a04e4b55f21faaba0cee26e4bcc4ace7"
RULESET_ID="1fbc0eef47bb469ea2604d41f4822f52"
TOKEN="$CLOUDFLARE_ZONE_MGMT_TOKEN"

# Read current rules before editing
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/rulesets/$RULESET_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Replace all rules (PUT = full replacement — always read first, modify, PUT back)
# Correct www→apex rule: 301, sends to https://, preserves path + query
curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/rulesets/$RULESET_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {
        "description": "Redirect www to root (301)",
        "expression": "(http.host eq \"www.mtbakermedical.com\")",
        "action": "redirect",
        "action_parameters": {
          "from_value": {
            "target_url": {
              "expression": "concat(\"https://mtbakermedical.com\", http.request.uri.path)"
            },
            "status_code": 301,
            "preserve_query_string": true
          }
        },
        "enabled": true
      }
    ]
  }'
```

**PUT replaces all rules.** If there are multiple rules, read first, edit the one you want, and PUT the full array.

**Known issue as of T16 (2026-06-12)**: the live redirect rule uses 302 (not 301) and redirects to `http://` apex (not `https://`), causing a 2-hop chain for `http://www` visitors. The rule template above fixes both. Needs a properly-scoped token to apply.

### Operation 3: Cache purge

```bash
ZONE_ID="a04e4b55f21faaba0cee26e4bcc4ace7"
TOKEN="$CLOUDFLARE_ZONE_MGMT_TOKEN"

# Purge specific URLs (preferred — surgical)
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/purge_cache" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"files": ["https://mtbakermedical.com/", "https://mtbakermedical.com/services/"]}'

# Purge everything (use sparingly — all traffic hits origin until re-cached)
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/purge_cache" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"purge_everything": true}'
```

### Zone settings (read/write)

```bash
# Read a setting
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/always_use_https" \
  -H "Authorization: Bearer $TOKEN"

# Enable Always Use HTTPS
curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/always_use_https" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "on"}'

# Read SSL mode (should be "full" or "full_strict")
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/ssl" \
  -H "Authorization: Bearer $TOKEN"
```

### mtbakermedical.com DNS state (T16, 2026-06-12)

Determined via `dig` (API token lacked DNS:Read at time of T16):

| Record | Type | Value | Proxied |
|---|---|---|---|
| `@` (apex) | A | `185.158.133.1` | Proxied (CF-RAY confirmed; x-deployment-id on 200 → CF Pages/Workers Assets) |
| `www` | A | `172.67.218.237`, `104.21.24.129` | Proxied (standard CF anycast IPs) |
| `@` | NS | `carioca.ns.cloudflare.com`, `ganz.ns.cloudflare.com` | — |
| `@` | MX | Google (5 records, priority 1/5/5/10/10) | — |
| `@` | TXT | `v=spf1 include:_spf.google.com ~all` | — |

Apex IP `185.158.133.1` is not in CF's published IP list but CF-RAY is present on all apex responses — CF is in the path using a non-advertised range, consistent with CF Pages/Workers Assets. Confirmed by `x-deployment-id` response header on the final 200.

### Live redirect state (T16, 2026-06-12)

| Request | Response | Notes |
|---|---|---|
| `http://www.mtbakermedical.com` | 302 → `http://mtbakermedical.com/` | CF-RAY ✓; 302 not 301; redirects to http not https |
| `https://www.mtbakermedical.com` | 302 → `https://mtbakermedical.com/` | CF-RAY ✓; 302 not 301 |
| `http://mtbakermedical.com` | 301 → `https://mtbakermedical.com/` | CF-RAY ✓; Always Use HTTPS confirmed on |
| `https://mtbakermedical.com` | 200 | CF-RAY ✓; final destination |

Full chain for `http://www`: 302 → 301 → 200 (3 hops). Fix: update the redirect rule to use 301 + `https://` target (template above).
