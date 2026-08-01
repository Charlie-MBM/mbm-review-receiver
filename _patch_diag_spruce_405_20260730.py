#!/usr/bin/env python3
"""Fix the false-negative liveness verdict in _diag_spruce_endpoint.py (v2 -> v3).

Run from mbm-review-receiver:  py _patch_diag_spruce_405_20260730.py

THE BUG (observed 2026-07-29):
v2 calls its liveness probe "THE AUTHORITATIVE TEST":

    GET /internalendpoints/{id}/conversations

That route is POST-only. Spruce answers **405 Method Not Allowed**, which falls
through every branch of the verdict tree to:

    print(f"  Inconclusive (status {rc}). Do not act on this either way.")
    return 7

So the one script whose whole job is to answer "are our automated texts alive?"
returns "don't know" on a perfectly healthy endpoint, every single time. That is
worse than cosmetic: 404 (the real silent-failure mode) and 405 (nothing wrong)
both read as "not 200", and the only thing separating them is a branch that did
not exist. A tired reader at 11pm sees a non-green verdict and starts debugging a
Spruce outage that isn't happening.

WHY NOT just add `if rc == 405: print("fine")`:
405 is emitted by method routing, which on most APIs runs BEFORE the path
parameter is resolved. A dead endpoint id would 405 too. 405 therefore proves the
ROUTE exists; it proves nothing about the ID. Treating it as "live" would convert
a false negative into a false positive, which is the strictly worse trade for a
check whose failure mode is silent.

THE FIX: promote the test that already works and already resolves the id.
`GET /internalendpoints` returns 200 today, and v2 already walks every endpoint
object for the id string into `matched`. If our configured id appears in the live
endpoint list, the id is live -- that is a direct, positive, GET-only observation
with no new route to guess at. It becomes the authoritative test. The
conversations probe is demoted to a corroborating signal and 405 is named for
what it is rather than being swept into "inconclusive".

New verdict precedence:
  1. id present in a 200 endpoint list      -> LIVE   (exit 0)
  2. conversations probe returned 200       -> LIVE   (exit 0)
  3. conversations probe returned 404       -> DEAD   (exit 5)
  4. conversations probe returned 401/403   -> AUTH   (exit 6)
  5. list was 200 but the id is NOT in it   -> PROBABLY STALE (exit 8, new)
  6. anything else                          -> inconclusive (exit 7)

Case 5 is new and is the one that actually matters: a 200 list that does not
contain our id is the signature of a repointed or deleted line, and v2 could not
express it.

NOT CHANGED: no new HTTP routes are invented, no request is added, PHI handling is
untouched (status codes only, conversation bodies never read), and the stale
.env-comment note still prints on a live verdict.

Count-asserted; aborts before writing on any mismatch. Backs up the file and
py_compiles the result.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "_diag_spruce_endpoint.py"
STAMP = "20260730-405"
GUARD = "PROBABLY STALE"

EDITS = [
    # 1 -- version note at the top of the docstring
    (
        '''"""Is SPRUCE_INTERNAL_ENDPOINT_ID pointing at a live line, and IS IT THE MAIN LINE?

v2 (2026-07-29)''',
        '''"""Is SPRUCE_INTERNAL_ENDPOINT_ID pointing at a live line, and IS IT THE MAIN LINE?

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

v2 (2026-07-29)''',
        1, "docstring: v3 note",
    ),
    # 2 -- capture the list status code
    (
        '''    print(f"\\n=== GET {BASE}/internalendpoints ===")
    matched = None
    try:
        r = http.get(f"{BASE}/internalendpoints", headers=H, timeout=30)
        print(f"  status {r.status_code}")''',
        '''    print(f"\\n=== GET {BASE}/internalendpoints ===   (THE AUTHORITATIVE TEST)")
    matched = None
    list_rc = None
    try:
        r = http.get(f"{BASE}/internalendpoints", headers=H, timeout=30)
        list_rc = r.status_code
        print(f"  status {list_rc}")''',
        1, "capture list_rc",
    ),
    # 3 -- demote the conversations probe
    (
        '''    # --- THE AUTHORITATIVE TEST. Always runs. Status code only -- body is PHI. ---
    print(f"=== GET {BASE}/internalendpoints/{{id}}/conversations   (status only) ===")''',
        '''    # --- CORROBORATING PROBE ONLY. This route is POST-only, so the healthy
    # answer is 405, not 200. A 405 proves the route exists; it does NOT prove our
    # id resolves, because method routing runs before path-parameter resolution.
    # The authoritative answer is list membership above. Status code only -- the
    # body is PHI and is never read.
    print(f"=== GET {BASE}/internalendpoints/{{id}}/conversations   (corroborating; POST-only route, 405 expected) ===")''',
        1, "demote the conversations probe",
    ),
    # 4 -- the verdict tree
    (
        '''    print("\\n=== VERDICT ===")
    if rc == 200:
        print("  LIVE. The endpoint id resolves and is usable. sendSms works.")
        if matched is None:
            print("  (The list above did not surface a matching id -- that is a listing")
            print("   shape quirk, not a fault. The 200 above is the real answer.)")
        print("  NOTE: the .env COMMENT still says this is the (360) 295-9241 line.")
        print("  The comment is stale, not the value. Worth correcting so nobody")
        print("  'fixes' a working id later.")
        return 0
    if rc == 404:
        print("  *** DEAD. 404 -- this is the silent-failure mode. Every automated")
        print("  text is failing, including the alerts that would tell you. ***")
        return 5
    if rc in (401, 403):
        print("  *** AUTH problem, not an endpoint problem. Check SPRUCE_API_KEY. ***")
        return 6
    print(f"  Inconclusive (status {rc}). Do not act on this either way.")
    return 7''',
        '''    def _env_comment_note():
        print("  NOTE: the .env COMMENT still says this is the (360) 295-9241 line.")
        print("  The comment is stale, not the value. Worth correcting so nobody")
        print("  'fixes' a working id later.")

    print("\\n=== VERDICT ===")
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
    return 7''',
        1, "verdict tree: authoritative=list membership, 405 named, new exit 8",
    ),
]


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found.", file=sys.stderr)
        print("Run this from mbm-review-receiver.", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if GUARD in s:
        print("ABORT: already patched (v3). Nothing to do.")
        return 1

    for old, new, want, label in EDITS:
        got = s.count(old)
        if got != want:
            print(f"ABORT: '{label}' matched {got} time(s), expected {want}. "
                  f"File not modified.", file=sys.stderr)
            return 3

    for old, new, want, label in EDITS:
        s = s.replace(old, new, want)
        print(f"  ok  {label}")

    # The old tree's only unconditional exit was `return 7`; the new one must still
    # have exactly one, plus the new 8. Cheap structural sanity check.
    for tok, want in (("return 8", 1), ("return 7", 1), ("return 5", 1), ("return 6", 1)):
        got = s.count(tok)
        if got != want:
            print(f"ABORT: post-edit sanity: '{tok}' appears {got} time(s), "
                  f"expected {want}. File not modified.", file=sys.stderr)
            return 4

    bak = TARGET.with_name(f"_diag_spruce_endpoint.py.bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8", newline="")
    TARGET.write_text(s.replace("\n", "\r\n") if crlf else s,
                      encoding="utf-8", newline="")

    import py_compile
    py_compile.compile(str(TARGET), doraise=True)

    print(f"\nPATCHED {TARGET.name}  (backup {bak.name}, syntax OK)")
    print("Next:  py _diag_spruce_endpoint.py   -- should now read LIVE, not Inconclusive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
