#!/usr/bin/env python3
"""Is SPRUCE_INTERNAL_ENDPOINT_ID pointing at a live line, and IS IT THE MAIN LINE?

v3 (2026-07-30) -- v2 ALWAYS RETURNED "INCONCLUSIVE". Its "authoritative" probe,
GET /internalendpoints/{id}/conversations, hits a POST-only route and answers 405,
which matched no branch of the verdict tree. The authoritative test is now
membership of our configured id in the 200 response from GET /internalendpoints --
a positive, GET-only observation that actually resolves the id. The conversations
probe is kept as corroboration only: 405 proves the ROUTE exists, not that the ID
does (method routing generally precedes path-parameter resolution), so it is
reported as "not a liveness signal" rather than silently counted either way. New
exit 8 = the list came back 200 and our id was NOT in it, which is what a
repointed or deleted line looks like and which v2 could not express.

v2 (2026-07-29) -- v1 REPORTED A FALSE "STALE" ALARM. It looked for an `id` key at
the top level of each endpoint object, but Spruce's shape is
{additionalMembers, endpoint, object, owner} with the id nested inside `endpoint`.
No top-level id was ever found, so nothing matched and it cried stale. v2 walks the
whole object for the id string, decodes the base64 id (it embeds the phone number),
and ALWAYS runs the liveness check -- which is the authoritative test and which v1
skipped by returning early on its own false verdict.

PHI-safe: prints your own practice line numbers and HTTP status codes only. Never
reads a conversation body, a patient name, or a patient phone.

Run:  py _diag_spruce_endpoint.py
"""
import base64
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("ABORT: python-dotenv not installed.", file=sys.stderr)
    raise SystemExit(2)
import requests as http

HERE = Path(__file__).resolve().parent
load_dotenv(dotenv_path=HERE / ".env")

KEY = os.environ.get("SPRUCE_API_KEY", "")
EID = os.environ.get("SPRUCE_INTERNAL_ENDPOINT_ID", "")
BASE = os.environ.get("SPRUCE_BASE_URL", "https://api.sprucehealth.com/v1")

MAIN_LINE = "3604987529"     # (360) 498-7529 -- the ONLY line automated texts may use
OTHER_LINE = "3602959241"    # (360) 295-9241 -- the line the .env COMMENT mentions


def digits(s):
    return re.sub(r"\D", "", str(s or ""))


def try_b64(s):
    """Spruce endpoint ids are base64 and embed the phone number. Decode if possible."""
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
        if all(32 <= ord(c) < 127 for c in raw):
            return raw
    except Exception:
        pass
    return None


def walk_strings(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            walk_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_strings(v, out)
    elif isinstance(obj, str):
        out.append(obj)


def phones_in(obj):
    found = set()
    strs = []
    walk_strings(obj, strs)
    for s in strs:
        d = digits(s)
        if len(d) in (10, 11) and d.endswith(("7529", "9241")) or (len(d) in (10, 11) and s.strip().startswith("+1")):
            found.add(s)
        elif re.match(r"^\(\d{3}\) \d{3}-\d{4}$", s.strip()):
            found.add(s.strip())
    return sorted(found)


def main():
    if not KEY:
        print("ABORT: SPRUCE_API_KEY missing from .env", file=sys.stderr)
        return 2
    print(f"SPRUCE_INTERNAL_ENDPOINT_ID in .env : {EID or '(EMPTY -- all texts are dead)'}")
    dec = try_b64(EID)
    if dec:
        print(f"  decodes to                        : {dec}")
        d = digits(dec)
        if MAIN_LINE in d:
            print(f"  -> embeds the MAIN LINE (360) 498-7529. Correct line.")
        elif OTHER_LINE in d:
            print(f"  -> embeds (360) 295-9241, NOT the main line. Repoint it.")
        else:
            print("  -> no recognizable practice number embedded.")
    if not EID:
        return 3

    H = {"Authorization": f"Bearer {KEY}"}

    print(f"\n=== GET {BASE}/internalendpoints ===   (THE AUTHORITATIVE TEST)")
    matched = None
    list_rc = None
    try:
        r = http.get(f"{BASE}/internalendpoints", headers=H, timeout=30)
        list_rc = r.status_code
        print(f"  status {list_rc}")
        if r.status_code == 200:
            data = r.json()
            eps = (data.get("internalEndpoints") or data.get("endpoints")
                   or data.get("data") or (data if isinstance(data, list) else []))
            print(f"  endpoints found: {len(eps)}\n")
            for ep in eps:
                strs = []
                walk_strings(ep, strs)
                is_me = EID in strs
                if is_me:
                    matched = ep
                print(f"  phone : {', '.join(phones_in(ep)) or '(not exposed)'}"
                      f"{'   <<< THIS IS THE ONE IN .env' if is_me else ''}")
                print(f"  keys  : {sorted(ep.keys()) if isinstance(ep, dict) else type(ep).__name__}")
                epi = (ep or {}).get("endpoint") if isinstance(ep, dict) else None
                if isinstance(epi, dict):
                    print(f"  endpoint.keys : {sorted(epi.keys())}")
                    print(f"  endpoint.id   : {epi.get('id')}")
                print()
        else:
            print(f"  body (first 300): {r.text[:300]}")
    except Exception as e:
        print(f"  request failed: {e}")

    # --- CORROBORATING PROBE ONLY. This route is POST-only, so the healthy
    # answer is 405, not 200. A 405 proves the route exists; it does NOT prove our
    # id resolves, because method routing runs before path-parameter resolution.
    # The authoritative answer is list membership above. Status code only -- the
    # body is PHI and is never read.
    print(f"=== GET {BASE}/internalendpoints/{{id}}/conversations   (corroborating; POST-only route, 405 expected) ===")
    rc = None
    try:
        r2 = http.get(f"{BASE}/internalendpoints/{EID}/conversations",
                      headers=H, params={"pageSize": 1}, timeout=30)
        rc = r2.status_code
        print(f"  status {rc}   (body deliberately not read -- it is PHI)")
    except Exception as e:
        print(f"  request failed: {e}")

    def _env_comment_note():
        print("  NOTE: the .env COMMENT still says this is the (360) 295-9241 line.")
        print("  The comment is stale, not the value. Worth correcting so nobody")
        print("  'fixes' a working id later.")

    print("\n=== VERDICT ===")
    if rc == 405:
        print("  (The conversations probe returned 405 -- that route is POST-only.")
        print("   Expected, and not a liveness signal either way. Ignoring it.)")

    if matched is not None:
        print("  LIVE. Our configured id appears in the live endpoint list, so the")
        print("  id resolves and sendSms has a real line to send from.")
        _env_comment_note()
        return 0
    if rc == 200:
        print("  LIVE. The conversations probe resolved the id.")
        print("  (The list above did not surface a matching id -- a listing shape")
        print("   quirk. The 200 is the real answer.)")
        _env_comment_note()
        return 0
    if rc == 404:
        print("  *** DEAD. 404 -- this is the silent-failure mode. Every automated")
        print("  text is failing, including the alerts that would tell you. ***")
        return 5
    if rc in (401, 403):
        print("  *** AUTH problem, not an endpoint problem. Check SPRUCE_API_KEY. ***")
        return 6
    if list_rc == 200:
        print("  *** PROBABLY STALE. The endpoint list came back 200 and our id was")
        print("  NOT in it. That is what a repointed or deleted line looks like.")
        print("  Compare the phone numbers printed above against the main line")
        print("  (360) 498-7529 and repoint SPRUCE_INTERNAL_ENDPOINT_ID if needed.")
        print("  Do NOT assume texts are flowing until this reads LIVE. ***")
        return 8
    print(f"  Inconclusive (list {list_rc}, probe {rc}). Neither check could reach a")
    print("  usable answer -- most likely a network or auth failure above, not a")
    print("  dead endpoint. Do not act on this either way.")
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
