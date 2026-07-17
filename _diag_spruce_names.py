#!/usr/bin/env python3
"""
_diag_spruce_names.py  -- READ ONLY. Diagnoses why some Hint patients show up
in Spruce as a bare phone number instead of their name. Reuses nurture_engine's
Hint + Spruce API calls + the same phone normalization. PII-REDACTED output
(first name + last initial; never a raw phone number). No writes.

Usage:
  py _diag_spruce_names.py
  py _diag_spruce_names.py --patient "Colleen Griffith"
"""
import argparse, re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
import nurture_engine as E


def redact(name):
    name = (name or "").strip()
    if not name:
        return "(no name)"
    p = name.split()
    return p[0] + (" " + p[-1][0] + "." if len(p) > 1 else "")


def hint_name(pt):
    return (pt.get("name")
            or " ".join(x for x in [pt.get("first_name"), pt.get("last_name")] if x)
            or pt.get("chosen_first_name") or "")


def looks_like_phone(s):
    s = (s or "").strip()
    if not s:
        return False
    digits = re.sub(r"\D", "", s)
    # displayName made up ONLY of phone punctuation/digits, with enough digits
    return len(digits) >= 7 and re.sub(r"[\d\s()+\-\.]", "", s) == ""


def spruce_phones(c):
    out = set()
    for pn in (c.get("phoneNumbers") or []):
        if isinstance(pn, dict):
            out.add(E.normalize_phone_e164(pn.get("value") or pn.get("displayValue")))
    return {p for p in out if p}


def real_name(c):
    dn = (c.get("displayName") or "").strip()
    return bool(dn) and not looks_like_phone(dn)


def hint_ids(c):
    return {l.get("externalId") for l in (c.get("integrationLinks") or [])
            if l.get("type") == "hint" and l.get("externalId")}


def classify(pt, by_link, phone_index):
    _, phones = E.patient_emails_phones(pt)
    c = by_link.get(pt.get("id"))
    if c is not None:
        return ("linked_named" if real_name(c) else "linked_no_realname"), phones, c
    match = next((phone_index[p] for p in phones if p in phone_index), None)
    if match is not None:
        return ("unlinked_named" if real_name(match) else "unlinked_number"), phones, match
    if not phones:
        return "no_phone_in_hint", phones, None
    return "no_spruce_contact", phones, None


LABELS = {
 "linked_named":      "OK   - real name + Hint-linked",
 "unlinked_number":   "BAD  - Spruce contact shows the PHONE NUMBER, not linked to Hint",
 "unlinked_named":    "WARN - has a name in Spruce but NOT linked to Hint (no Hint panel)",
 "linked_no_realname":"ODD  - Hint-linked but displayName is a number/blank",
 "no_spruce_contact": "n/a  - no Spruce contact yet (never texted)",
 "no_phone_in_hint":  "n/a  - no phone on Hint record",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient")
    args = ap.parse_args()

    print("Fetching (read-only)...")
    patients = E.hint_all_patients()
    contacts = E.spruce_list_contacts()
    print(f"  hint patients: {len(patients)}   spruce contacts: {len(contacts)}")

    by_link, phone_index = {}, {}
    phone_as_name = 0
    for c in contacts:
        for hid in hint_ids(c):
            by_link.setdefault(hid, c)
        for ph in spruce_phones(c):
            phone_index.setdefault(ph, c)
        if looks_like_phone(c.get("displayName")):
            phone_as_name += 1

    from collections import Counter
    tally, examples, targets_bucket = Counter(), {}, {}
    for pt in patients:
        cat, phones, _ = classify(pt, by_link, phone_index)
        tally[cat] += 1
        examples.setdefault(cat, redact(hint_name(pt)))

    print("\n=== How Hint patients resolve in Spruce ===")
    for k in ["linked_named","unlinked_number","unlinked_named","linked_no_realname",
              "no_spruce_contact","no_phone_in_hint"]:
        if tally.get(k):
            print(f"  {tally[k]:4d}  {LABELS[k]:62s}  e.g. {examples[k]}")
    print(f"\n  Spruce contacts whose displayName IS a phone number: {phone_as_name}")

    if args.patient:
        toks = [t for t in re.split(r"\s+", args.patient.strip().lower()) if t]
        hits = [pt for pt in patients
                if any(t in hint_name(pt).lower() for t in toks)]
        print(f"\n=== Target '{args.patient}'  ({len(hits)} name-token match(es)) ===")
        for pt in hits:
            cat, phones, c = classify(pt, by_link, phone_index)
            dups = [x for x in contacts if spruce_phones(x) & phones]
            print(f"  {redact(hint_name(pt))}  pat_id={pt.get('id')}  bucket={cat}")
            print(f"     hint_phones={len(phones)}  spruce_contacts_matching_her_phone={len(dups)}")
            for x in dups:
                print(f"       contact {x.get('id')}: real_name={real_name(x)} "
                      f"hint_linked={bool(hint_ids(x))} displayName_is_phone={looks_like_phone(x.get('displayName'))}")
            print(f"     => {LABELS.get(cat,'')}")
        if not hits:
            print("  no Hint record matched those name tokens — check spelling / stored name form")


if __name__ == "__main__":
    main()
