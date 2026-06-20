#!/usr/bin/env python3
"""T5c verification: unit checks (copy/cadence) + simulated state-machine paths
with stubbed Hint/Spruce (no network, no sends)."""
from datetime import datetime, timezone, timedelta
import nurture_engine as E
import send_nurture_sequence as S

fails = []
def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        fails.append(name)

# ---------- unit: copy is link-free, service-agnostic, James, Day21 STOP ----------
d0, d7, d21 = E.render(0, "Dan"), E.render(7, "Dan"), E.render(21, "Dan")
check("Day0 from James", d0.startswith("Hi Dan, this is James from Mt. Baker Medical."))
check("Day21 verbatim STOP", d21.endswith("It was a real pleasure meeting you. Reply STOP to opt out."))
check("no http link in any text", all("http" not in t for t in (d0, d7, d21)))
check("no GLP/ketamine/hormone words (service-agnostic)",
      all(w not in (d0+d7+d21).lower() for w in ("glp", "ketamine", "semaglutide", "hormone", "testosterone")))
check("fallback name 'there'", E.render(0, "").startswith("Hi there,"))

# ---------- unit: cadence + start-date ----------
today = datetime.now(timezone.utc)
ago = lambda n: (today - timedelta(days=n)).isoformat()
check("Day0 due at enroll", E.due_touch(ago(0), []) == 0)
check("Day7 due at day 8", E.due_touch(ago(8), [0]) == 7)
check("Day21 due at day 25", E.due_touch(ago(25), [0, 7]) == 21)
check("nothing due after all sent", E.due_touch(ago(30), [0, 7, 21]) is None)
check("start_date_passed past", E.start_date_passed("2026-01-01") is True)
check("start_date_passed future", E.start_date_passed("2026-12-31") is False)
check("denylist catches ZZTEST NurtureCheck", E.is_denylisted("ZZTEST NurtureCheck"))

# ---------- simulated state-machine paths ----------
E.DRY_RUN = True
GO_LIVE = (today - timedelta(days=1)).isoformat()  # yesterday

def mem(mem_id, created_days_ago=0, start="2026-12-31"):
    return {"mem_id": mem_id, "pat_id": "pat-FAKE", "patient_name": "Dan Prospect",
            "plan": "Concierge 2026", "start_date": start,
            "created_at": (today - timedelta(days=created_days_ago)).isoformat(), "status": "pending"}

# Stubs
E.hint_get_patient = lambda pid: {"chosen_first_name": "Dan",
                                  "phones": [{"number": "(360) 555-0000", "type": "mobile"}]}
E.spruce_contact_for_patient = lambda contacts, pid, ph: None  # no STOP thread
E.spruce_thread_has_opt_out = lambda cid: False

def run_case(name, mem_rec, fresh_status="pending", has_card=False, state=None, approved=None,
             review_today=False):
    E.hint_get_membership = lambda mid: (None if fresh_status == "GONE" else {"status": fresh_status})
    E.hint_has_payment_source = lambda pid: has_card
    E.review_asked_today = lambda pid: review_today
    return S.evaluate(mem_rec, [], state if state is not None else {"_meta": {}},
                      approved or set(), GO_LIVE, today)

# A: new (created after go-live) + eligible -> would_send Day 0
p = run_case("A", mem("mem-A", created_days_ago=0))
check("A new-eligible -> would_send Day0", p["action"] == "would_send" and p["day"] == 0
      and p["body_preview"].startswith("Hi Dan, this is James"))

# B: payment source lands -> exit
p = run_case("B", mem("mem-B", 0), has_card=True)
check("B payment source -> exit", p["action"] == "exit" and "payment source" in p["reason"])

# C: status active -> exit
p = run_case("C", mem("mem-C", 0), fresh_status="active")
check("C status active -> exit", p["action"] == "exit" and "active" in p["reason"])

# D: membership canceled/deleted -> exit
p = run_case("D", mem("mem-D", 0), fresh_status="GONE")
check("D canceled/deleted -> exit", p["action"] == "exit" and "canceled" in p["reason"])

# E: pre-existing (created before go-live) + not approved -> needs_approval
p = run_case("E", mem("mem-E", created_days_ago=5))
check("E pre-existing -> needs_approval", p["action"] == "needs_approval")

# F: pre-existing but APPROVED -> would_send Day 0
p = run_case("F", mem("mem-F", 5), approved={"mem-F"})
check("F pre-existing+approved -> would_send", p["action"] == "would_send" and p["day"] == 0)

# G: review poller asked today -> defer
p = run_case("G", mem("mem-G", 0), review_today=True)
check("G review-today -> defer", p["action"] == "defer" and p["day"] == 0)

# H: Day 7 due (enrolled 8 days ago, Day0 sent)
st = {"_meta": {}, "mem-H": {"pat_id": "pat-FAKE", "plan": "Concierge 2026",
      "start_date": "2026-12-31", "created_at": GO_LIVE,
      "enrolled_at": (today - timedelta(days=8)).isoformat(), "touches_sent": [0], "status": "active"}}
p = run_case("H", mem("mem-H", 0), state=st)
check("H Day7 due -> would_send Day7", p["action"] == "would_send" and p["day"] == 7)

# I: all sent + start date passed -> completed + staff flag
st = {"_meta": {}, "mem-I": {"pat_id": "pat-FAKE", "plan": "Concierge 2026",
      "start_date": "2026-01-01", "created_at": GO_LIVE,
      "enrolled_at": (today - timedelta(days=30)).isoformat(), "touches_sent": [0, 7, 21], "status": "active"}}
p = run_case("I", mem("mem-I", 0, start="2026-01-01"), state=st)
check("I completed + staff flag", p["action"] == "completed" and "STAFF FLAG" in (p.get("reason") or "")
      and st["mem-I"].get("staff_flagged") is True)

# J: denylisted dummy -> skip
p = run_case("J", {**mem("mem-J", 0), "patient_name": "ZZTEST NurtureCheck"})
check("J denylist -> skip", p["action"] == "skip" and "denylist" in p["reason"])

print("\nTOTAL:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
