#!/usr/bin/env python3
"""
_fix_spruce_links.py -- Make Hint patients show their NAME in Spruce instead of
a bare phone number.

Two modes (the integration-link endpoint returns 500 for this account's managed
Hint integration, so NAME is the default/reliable fix):

  --mode name  (default): PATCH /v1/contacts/{id} with givenName/familyName from
               the Hint record. Sets the visible name directly.
  --mode link           : POST /v1/contacts/{id}/integrationlinks (type=hint).
               Kept for testing; currently 500s on this account.

SAFE: dry-run unless --apply. Test one first with --only <contact_id> or
--limit 1. Redacted output; runs on your machine (no PHI through Cowork).

  py _fix_spruce_links.py                                   # dry run, all
  py _fix_spruce_links.py --only entity_2P2R47LSSQ000 --apply   # just Colleen
  py _fix_spruce_links.py --apply                           # all (after test)
"""
import argparse, re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import nurture_engine as E
import requests


def redact(n):
    n = (n or "").strip()
    if not n:
        return "(no name)"
    p = n.split()
    return p[0] + (" " + p[-1][0] + "." if len(p) > 1 else "")


def given_family(pt):
    given = (pt.get("chosen_first_name") or pt.get("first_name") or "").strip()
    family = (pt.get("last_name") or "").strip()
    if not given and not family:
        nm = (pt.get("name") or "").strip().split()
        if nm:
            given, family = nm[0], (nm[-1] if len(nm) > 1 else "")
    return given, family


def spruce_phones(c):
    out = set()
    for pn in (c.get("phoneNumbers") or []):
        if isinstance(pn, dict):
            out.add(E.normalize_phone_e164(pn.get("value") or pn.get("displayValue")))
    return {p for p in out if p}


def looks_like_phone(s):
    s = (s or "").strip()
    if not s:
        return False
    return len(re.sub(r"\D", "", s)) >= 7 and re.sub(r"[\d\s()+\-\.]", "", s) == ""


def real_name(c):
    dn = (c.get("displayName") or "").strip()
    return bool(dn) and not looks_like_phone(dn)


def hint_ids(c):
    return {l.get("externalId") for l in (c.get("integrationLinks") or [])
            if l.get("type") == "hint" and l.get("externalId")}


def set_name(contact_id, given, family):
    url = f"{E.SPRUCE_BASE_URL}/contacts/{contact_id}"
    body = {}
    if given:
        body["givenName"] = given
    if family:
        body["familyName"] = family
    r = requests.patch(url, headers={**E._spruce_headers(), "Content-Type": "application/json"},
                       json=body, timeout=30)
    return r.status_code, (r.text or "")[:180]


def create_link(contact_id, pat_id):
    url = f"{E.SPRUCE_BASE_URL}/contacts/{contact_id}/integrationlinks"
    headers = {**E._spruce_headers(), "Content-Type": "application/json",
               "s-idempotency-key": f"link-{contact_id}-{pat_id}"[:255]}
    r = requests.post(url, headers=headers, json={"type": "hint", "externalId": pat_id}, timeout=30)
    return r.status_code, (r.text or "")[:180]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["name", "link"], default="name")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", help="restrict to a single Spruce contact id")
    ap.add_argument("--limit", type=int, default=0, help="cap how many to act on (0=all)")
    args = ap.parse_args()

    print(f"Mode={args.mode}  {'APPLY' if args.apply else 'DRY RUN'}"
          + (f"  only={args.only}" if args.only else "")
          + (f"  limit={args.limit}" if args.limit else ""))
    patients = E.hint_all_patients()
    contacts = E.spruce_list_contacts()
    linked = set().union(*[hint_ids(c) for c in contacts]) if contacts else set()

    phone_index = {}
    for c in contacts:
        for ph in spruce_phones(c):
            phone_index.setdefault(ph, c)

    plan = []
    for pt in patients:
        if pt.get("id") in linked:
            continue
        _, phones = E.patient_emails_phones(pt)
        cands = [c for c in contacts if (spruce_phones(c) & phones) and not hint_ids(c)]
        if len(cands) != 1:
            continue
        c = cands[0]
        if real_name(c):          # already displays a real name; skip
            continue
        if args.only and c.get("id") != args.only:
            continue
        plan.append((pt, c))
    if args.limit:
        plan = plan[:args.limit]

    print(f"\nTargets: {len(plan)}")
    for pt, c in plan:
        g, f = given_family(pt)
        print(f"  {redact(pt.get('name') or (g+' '+f)):18s} contact={c.get('id')}"
              + (f"  set name-> {redact(g+' '+f)}" if args.mode == 'name' else f"  link-> {pt.get('id')}"))

    if not args.apply:
        print("\nDRY RUN. add --apply to write. Tip: test one with --only <id> --apply first.")
        return

    print("\n=== Applying ===")
    ok = fail = 0
    for pt, c in plan:
        if args.mode == "name":
            g, f = given_family(pt)
            code, msg = set_name(c.get("id"), g, f)
        else:
            code, msg = create_link(c.get("id"), pt.get("id"))
        if code in (200, 201, 204, 422):
            ok += 1
            print(f"  OK   {redact(pt.get('name')):18s} contact={c.get('id')} ({code})")
        else:
            fail += 1
            print(f"  FAIL {redact(pt.get('name')):18s} contact={c.get('id')} ({code}) {msg}")
    print(f"\nDone. ok={ok}  failed={fail}")


if __name__ == "__main__":
    main()
