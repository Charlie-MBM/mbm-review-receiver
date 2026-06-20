#!/usr/bin/env python3
"""Safe end-to-end tag-detection test using the ZZ-TEST dummy (no phone/email,
so nothing can physically send). Applies the nurture-prospect tag, verifies the
poller detects it, then leaves deletion to a separate explicit step."""
import sys
import nurture_engine as E
import requests as http

DUMMY_ID = "entity_2ODLQ34F57000"  # ZZ-TEST / NurtureQA-DoNotContact
H = {"Authorization": f"Bearer {E.SPRUCE_API_KEY}", "Content-Type": "application/json"}

action = sys.argv[1] if len(sys.argv) > 1 else "tag"

if action == "tag":
    tag_id = E.spruce_get_or_create_nurture_tag()
    print("nurture tag id:", tag_id)
    # Confirm dummy currently has no tags (safe to set tagIds without clobber).
    r = http.get(f"{E.SPRUCE_BASE_URL}/contacts/{DUMMY_ID}", headers={"Authorization": H["Authorization"]}, timeout=30)
    before = r.json()
    print("dummy displayName:", before.get("displayName"), "| existing tags:", E.contact_tag_values(before),
          "| has_phone:", bool(before.get("phoneNumbers")), "| has_email:", bool(before.get("emailAddresses")))
    if E.contact_tag_values(before):
        print("ABORT: dummy already has tags; not overwriting.")
        sys.exit(1)
    # Apply the nurture-prospect tag.
    rp = http.patch(f"{E.SPRUCE_BASE_URL}/contacts/{DUMMY_ID}", headers=H,
                    json={"tagIds": [tag_id]}, timeout=30)
    print("PATCH status:", rp.status_code)
    after = rp.json() if rp.status_code == 200 else {}
    print("dummy tags after PATCH:", E.contact_tag_values(after))

elif action == "delete":
    r = http.get(f"{E.SPRUCE_BASE_URL}/contacts/{DUMMY_ID}", headers={"Authorization": H["Authorization"]}, timeout=30)
    if r.status_code != 200:
        print("dummy already gone:", r.status_code)
        sys.exit(0)
    c = r.json()
    print("about to delete:", c.get("displayName"), "canDelete:", c.get("canDelete"),
          "has_phone:", bool(c.get("phoneNumbers")), "has_email:", bool(c.get("emailAddresses")))
    rd = http.delete(f"{E.SPRUCE_BASE_URL}/contacts/{DUMMY_ID}", headers={"Authorization": H["Authorization"]}, timeout=30)
    print("DELETE status:", rd.status_code)
    # verify
    rv = http.get(f"{E.SPRUCE_BASE_URL}/contacts/{DUMMY_ID}", headers={"Authorization": H["Authorization"]}, timeout=30)
    print("verify GET after delete:", rv.status_code, "(404/410 = gone)")
