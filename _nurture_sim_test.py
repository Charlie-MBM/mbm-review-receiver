#!/usr/bin/env python3
"""Simulated integration test: stub Hint/Spruce so we can exercise the full
evaluate_contact decision path without touching real data or sending. Verifies:
  - inactive prospect with phone -> would_send Day 0 (dry-run)
  - active membership -> suppress
  - opt-out in thread -> suppress
  - review poller asked today -> defer
"""
from datetime import datetime, timezone, timedelta
import nurture_engine as E
import send_nurture_sequence as S

E.DRY_RUN = True
today = datetime.now(timezone.utc)

def contact(cid, name="Jane Prospect", pat="pat-FAKE", phone=True):
    return {
        "id": cid, "displayName": name, "givenName": name.split()[0],
        "category": "patient",
        "phoneNumbers": [{"value": "+13605551234", "displayValue": "(360) 555-1234", "label": "Mobile"}] if phone else [],
        "tags": [{"value": "nurture-prospect"}],
        "integrationLinks": [{"type": "hint", "externalId": pat}],
    }

# Stub Hint patient -> inactive, with name+phone
def stub_inactive(pat_id):
    return {"membership_status": "inactive", "chosen_first_name": "Jane",
            "phones": [{"number": "(360) 555-1234", "type": "mobile"}]}
def stub_active(pat_id):
    return {"membership_status": "active", "chosen_first_name": "Jane",
            "phones": [{"number": "(360) 555-1234", "type": "mobile"}]}

results = {}

# Case A: inactive + phone + no opt-out + review NOT today -> would_send Day 0
E.hint_get_patient = stub_inactive
E.spruce_thread_has_opt_out = lambda cid: False
E.review_asked_today = lambda pat: False
st = {"_meta": {}}
plan = S.evaluate_contact(contact("entity_A"), st, today)
results["A inactive->would_send Day0"] = (plan["action"] == "would_send" and plan["day"] == 0
                                          and plan["body_preview"].startswith("Hi Jane, this is James"))

# Case B: active membership -> suppress
E.hint_get_patient = stub_active
plan = S.evaluate_contact(contact("entity_B"), {"_meta": {}}, today)
results["B active->suppress"] = (plan["action"] == "suppress" and "membership active" in plan["reason"])

# Case C: inactive but opt-out in thread -> suppress
E.hint_get_patient = stub_inactive
E.spruce_thread_has_opt_out = lambda cid: True
plan = S.evaluate_contact(contact("entity_C"), {"_meta": {}}, today)
results["C optout->suppress"] = (plan["action"] == "suppress" and "opt-out" in plan["reason"])

# Case D: inactive, no opt-out, but review poller asked today -> defer
E.spruce_thread_has_opt_out = lambda cid: False
E.review_asked_today = lambda pat: True
plan = S.evaluate_contact(contact("entity_D"), {"_meta": {}}, today)
results["D review-today->defer"] = (plan["action"] == "defer" and plan["day"] == 0)

# Case E: Day 7 due (enrolled 8 days ago, Day 0 already sent)
E.review_asked_today = lambda pat: False
st = {"_meta": {}, "entity_E": {"pat_id": "pat-FAKE",
      "enrolled_at": (today - timedelta(days=8)).isoformat(),
      "touches_sent": [0], "status": "active"}}
plan = S.evaluate_contact(contact("entity_E"), st, today)
results["E Day7 due->would_send"] = (plan["action"] == "would_send" and plan["day"] == 7)

print()
allpass = True
for k, v in results.items():
    print(("PASS" if v else "FAIL"), "-", k)
    allpass = allpass and v
print("\nTOTAL:", "ALL PASS" if allpass else "SOME FAILED")
