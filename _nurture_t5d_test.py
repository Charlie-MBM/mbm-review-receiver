#!/usr/bin/env python3
"""T5d verification suite. Imports the fresh-inode copies (the sandbox mount
caches a stale view of the overwritten canonical files; host files are identical
and are what Windows runs). Covers the carried-over T5c assertions plus all the
new T5d paths: per-plan link, cross-record suppression, reconcile-once, and each
Part C cleanup guard."""
from datetime import datetime, timezone, timedelta
import _ne_t5d as E
import _sns_run as S

fails = []
def check(n, c):
    print(("PASS" if c else "FAIL"), "-", n)
    if not c:
        fails.append(n)

CONCIERGE_URL = "https://mtbakermedical.hint.com/signup/concierge"
today = datetime.now(timezone.utc)
ago = lambda d: (today - timedelta(days=d)).isoformat()

# ---------- copy / helpers (carried from T5c, adapted) ----------
d0lf, d7, d21 = E.render(0, "Dan"), E.render(7, "Dan"), E.render(21, "Dan")
d0link = E.render(0, "Dan", CONCIERGE_URL)
check("Day0 link-free from James", d0lf.startswith("Hi Dan, this is James from Mt. Baker Medical."))
check("Day0 LINK contains url + phrase", CONCIERGE_URL in d0link and "here's your signup link" in d0link)
check("Day0 no-url is link-free (no http)", "http" not in d0lf)
check("Day21 verbatim STOP", d21.endswith("It was a real pleasure meeting you. Reply STOP to opt out."))
check("service-agnostic (no glp/ketamine/hormone)",
      all(w not in (d0lf+d0link+d7+d21).lower() for w in ("glp","ketamine","semaglutide","hormone","testosterone")))
check("fallback 'there'", E.render(0, "").startswith("Hi there,"))
check("E164 10-digit", E.normalize_phone_e164("(360) 349-8094") == "+13603498094")
check("E164 11-digit", E.normalize_phone_e164("1-360-349-8094") == "+13603498094")
check("E164 empty", E.normalize_phone_e164("") == "")
check("due Day0", E.due_touch(ago(0), []) == 0)
check("due Day7", E.due_touch(ago(8), [0]) == 7)
check("due Day21", E.due_touch(ago(25), [0,7]) == 21)
check("none after all", E.due_touch(ago(40), [0,7,21]) is None)
check("catch-up latest", E.due_touch(ago(25), []) == 21)
check("start_date past", E.start_date_passed("2026-01-01") is True)
check("start_date future", E.start_date_passed("2026-12-31") is False)
check("denylist ZZTEST NurtureCheck", E.is_denylisted("ZZTEST NurtureCheck"))
check("plan_family concierge", E.plan_family("Concierge 2026") == "concierge" == E.plan_family("Concierge"))
check("plan_family glp", E.plan_family("SO - GLP-1 Semaglutide") == "glp")
check("signup_url known", E.signup_url_for("pln-xjukKCU9Xf6M") == CONCIERGE_URL)
check("signup_url unknown None", E.signup_url_for("pln-OTHER") is None)

# ---------- evaluate-path sims (stub the network) ----------
E.DRY_RUN = True
GO_LIVE = ago(1)
CANCELS = {"n": 0}
E.hint_cancel_membership = lambda mid, reason: (CANCELS.__setitem__("n", CANCELS["n"] + 1) or True)
E.audit = lambda *a, **k: None
ARCH = {"n": 0}
E.archive_queue_add = lambda *a, **k: ARCH.__setitem__("n", ARCH["n"] + 1)
E.hint_get_patient = lambda pid: {"chosen_first_name": "Dan", "first_name": "Dan",
                                  "email": "dan@x.com", "phones": [{"number": "(360) 555-1212", "type": "mobile"}]}
E.spruce_contact_for_patient = lambda contacts, pid, ph: {"id": "entity_X"}
E.spruce_thread_has_opt_out = lambda cid: False
E.spruce_thread_has_any_inbound = lambda cid: False
E.review_asked_today = lambda pid: False
E.hint_has_future_appointment = lambda pid: False

def mem(mid, plan="Concierge 2026", plan_id="pln-xjukKCU9Xf6M", created_days=0, start="2026-12-31",
        name="Dan Prospect", pat="pat-OUR"):
    return {"mem_id": mid, "pat_id": pat, "patient_name": name, "plan": plan, "plan_id": plan_id,
            "start_date": start, "created_at": ago(created_days), "status": "pending"}

def rec(pid, statuses=(), plan="Concierge 2026", card=False):
    return {"id": pid, "email": "dan@x.com", "phones": [{"number": "(360) 555-1212", "type": "mobile"}],
            "memberships": [{"status": s, "plan": {"name": plan}} for s in statuses], "_card": card}

def run(mid_mem, matched, cards, fresh_status="pending", state=None, approved=None):
    E.hint_get_membership = lambda mid: (None if fresh_status == "GONE" else {"status": fresh_status, "bill_date": "2026-07-15"})
    E.match_records = lambda allp, em, ph: matched
    E.hint_has_payment_source = lambda pid: cards.get(pid, False)
    return S.evaluate(mid_mem, [], [], state if state is not None else {"_meta": {}}, approved or set(), GO_LIVE, today)

# new-eligible Concierge (consult was yesterday) -> would_send Day0 WITH link
CANCELS["n"] = 0
p = run(mem("mem-A", created_days=1), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("A new-eligible -> would_send Day0 + LINK",
      p["action"] == "would_send" and p["day"] == 0 and CONCIERGE_URL in p["body_preview"])

# created TODAY -> wait (Day 0 must fire the next morning, not same day as consult)
p = run(mem("mem-T0", created_days=0), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("T0 created today -> wait (next-day Day0)", p["action"] == "wait")

# plan WITHOUT url -> link-free Day0
p = run(mem("mem-B", plan="SO - GLP-1 Semaglutide", plan_id="pln-OTHER", created_days=1), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("B plan w/o url -> Day0 link-free", p["action"] == "would_send" and p["day"] == 0 and "http" not in p["body_preview"])

# cross-record CARD on duplicate -> reconcile_cancel (once)
CANCELS["n"] = 0
st = {"_meta": {}}
p = run(mem("mem-C"), [rec("pat-OUR", ["pending"]), rec("pat-DUP", [], card=True)], {"pat-OUR": False, "pat-DUP": True}, state=st)
check("C cross-record card -> reconcile_cancel", p["action"] == "reconcile_cancel" and CANCELS["n"] == 1 and st["mem-C"].get("reconciled") is True)

# reconcile fires EXACTLY once (second run -> exit, no extra cancel)
p2 = run(mem("mem-C"), [rec("pat-OUR", ["pending"]), rec("pat-DUP", [], card=True)], {"pat-OUR": False, "pat-DUP": True}, state=st)
check("C2 reconcile once only", p2["action"] == "exit" and CANCELS["n"] == 1)

# cross-record ACTIVE membership on duplicate -> reconcile_cancel
CANCELS["n"] = 0
p = run(mem("mem-D"), [rec("pat-OUR", ["pending"]), rec("pat-DUP", ["active"])], {"pat-OUR": False, "pat-DUP": False})
check("D cross-record active -> reconcile_cancel", p["action"] == "reconcile_cancel" and CANCELS["n"] == 1)

# our-own card only (no separate membership) -> exit, NO cancel
CANCELS["n"] = 0
p = run(mem("mem-E"), [rec("pat-OUR", ["pending"], card=True)], {"pat-OUR": True})
check("E own card only -> exit no cancel", p["action"] == "exit" and CANCELS["n"] == 0)

# our mem already active -> exit
p = run(mem("mem-F"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False}, fresh_status="active")
check("F status active -> exit", p["action"] == "exit")

# our mem gone -> exit
p = run(mem("mem-G"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False}, fresh_status="GONE")
check("G canceled/deleted -> exit", p["action"] == "exit")

# opt-out -> exit
E.spruce_thread_has_opt_out = lambda cid: True
p = run(mem("mem-H"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("H opt-out -> exit", p["action"] == "exit")
E.spruce_thread_has_opt_out = lambda cid: False

# review asked today -> defer
E.review_asked_today = lambda pid: True
p = run(mem("mem-I"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("I review-today -> defer", p["action"] == "defer" and p["day"] == 0)
E.review_asked_today = lambda pid: False

# pre-existing (created before go-live), not approved -> needs_approval
p = run(mem("mem-J", created_days=5), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("J pre-existing -> needs_approval", p["action"] == "needs_approval")

# Day7 due
st = {"_meta": {}, "mem-K": {"pat_id": "pat-OUR", "plan": "Concierge 2026", "plan_id": "pln-xjukKCU9Xf6M",
      "start_date": "2026-12-31", "created_at": GO_LIVE, "enrolled_at": ago(8), "touches_sent": [0], "status": "active"}}
p = run(mem("mem-K"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False}, state=st)
check("K Day7 due -> would_send Day7", p["action"] == "would_send" and p["day"] == 7)

# Dan I. hard-excluded
p = run(mem("mem-zhNHV8snambF", name="Dan Ingberman"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("Dan I. hard-excluded -> skip", p["action"] == "skip")

# denylisted dummy -> skip
p = run(mem("mem-Z", name="ZZTEST NurtureCheck"), [rec("pat-OUR", ["pending"])], {"pat-OUR": False})
check("dummy -> skip", p["action"] == "skip")

# ---------- Part C cleanup guards (call _handle_complete_or_wait directly) ----------
def cleanup(our, other, future_appt, inbound):
    E.hint_has_future_appointment = lambda pid: future_appt
    E.spruce_thread_has_any_inbound = lambda cid: inbound
    mid = "mem-CLN"
    st = {"_meta": {}, mid: {"enrolled_at": ago(31), "touches_sent": [0,7,21], "status": "completed"}}
    plan = {"mem": mid, "name": "Dan P.", "action": None, "reason": None, "day": None,
            "schedule": {}}
    return S._handle_complete_or_wait(plan, mem(mid, start="2026-12-31"), st, mid, "pat-OUR",
                                      [], our, other, {"id": "entity_X"}, ago(31), [0,7,21], "Dan P.")

clear = {"card": False, "active": False, "nonpend_fam": False}
CANCELS["n"] = 0; ARCH["n"] = 0
p = cleanup({"card": True, "active": False, "nonpend_fam": False}, clear, False, False)
check("guard: payment blocks cleanup", p["action"] == "cleanup_blocked" and "no_payment" in p["reason"])
p = cleanup(clear, {"card": False, "active": True, "nonpend_fam": False}, False, False)
check("guard: active membership blocks", p["action"] == "cleanup_blocked" and "no_active_membership" in p["reason"])
p = cleanup(clear, clear, True, False)
check("guard: future appointment blocks", p["action"] == "cleanup_blocked" and "no_future_appointment" in p["reason"])
p = cleanup(clear, clear, False, True)
check("guard: inbound reply blocks", p["action"] == "cleanup_blocked" and "no_inbound_reply" in p["reason"])
CANCELS["n"] = 0; ARCH["n"] = 0
p = cleanup(clear, clear, False, False)
check("all guards pass -> cleanup (cancel+archive)", p["action"] == "cleanup" and CANCELS["n"] == 1 and ARCH["n"] == 1)

print("\nTOTAL:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
