#!/usr/bin/env python3
"""
send_nurture_sequence.py - Daily CLI nurture poller (T5d). Sibling of the review
and consult-intake pollers; runs on Charlie's laptop via MBM-Nurture-Poller.
Trigger: Hint FUTURE-DATED PENDING memberships with NO payment source. T5d adds
per-plan Day-0 links, cross-record duplicate suppression + auto-cancel-once
reconcile, and a guarded Day-30 cleanup. DRY_RUN gates all sends AND cancels.
This GitHub copy is the authoritative backup of the local file the scheduler runs.
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

import nurture_engine as E
from nurture_engine import log

APPROVED_FILE = E.SCRIPT_DIR / "nurture_approved.json"
# Ratified (Charlie, 2026-06-10): Dan I. already said yes verbally, payment manual.
MEMBERSHIP_DENYLIST = {"mem-zhNHV8snambF"}


def parse_args():
    p = argparse.ArgumentParser(description="Daily pending-membership nurture poller (T5d).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--go-live", action="store_true")
    p.add_argument("--list-pending", action="store_true")
    return p.parse_args()


def load_approved():
    try:
        if APPROVED_FILE.exists():
            return set(json.loads(APPROVED_FILE.read_text()))
    except Exception as e:
        log.warning(f"could not read nurture_approved.json: {e}")
    return set()


def redact(name):
    parts = (name or "").strip().split()
    return (parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")) if parts else "(none)"


def list_pending():
    pend = E.hint_list_pending_memberships()
    rows = []
    for m in pend:
        has_card = E.hint_has_payment_source(m["pat_id"])
        rows.append({"mem": m["mem_id"], "patient": m["pat_id"], "name": redact(m["patient_name"]),
                     "plan": m["plan"], "plan_id": m["plan_id"], "start_date": m["start_date"],
                     "created_at": m["created_at"], "has_payment_source": has_card,
                     "signup_url": E.signup_url_for(m["plan_id"]),
                     "eligible": (not has_card) and (not E.is_denylisted(m["patient_name"])
                                                     and m["mem_id"] not in MEMBERSHIP_DENYLIST)})
    print(json.dumps({"pending": rows, "count": len(rows)}, indent=2, default=str))
    return rows


def conversion_scan(matched, our_pat_id, our_family):
    our = {"card": False, "active": False, "nonpend_fam": False}
    other = {"card": False, "active": False, "nonpend_fam": False}
    for pt in matched:
        pid = pt.get("id")
        card = E.hint_has_payment_source(pid) is True
        active, nonpend_fam = E.record_membership_signals(pt, our_family)
        tgt = our if pid == our_pat_id else other
        tgt["card"] = tgt["card"] or card
        tgt["active"] = tgt["active"] or active
        tgt["nonpend_fam"] = tgt["nonpend_fam"] or nonpend_fam
    return our, other


def evaluate(m, all_patients, contacts, state, approved, go_live_at, today):
    mem_id, pat_id = m["mem_id"], m["pat_id"]
    name_red = redact(m["patient_name"])
    plan = {"mem": mem_id, "name": name_red, "plan": m["plan"], "action": None, "reason": None, "day": None}

    if E.is_denylisted(m["patient_name"]):
        plan.update(action="skip", reason="denylist (test dummy)")
        return plan
    if mem_id in MEMBERSHIP_DENYLIST:
        plan.update(action="skip", reason="hard-excluded (ratified not-for-nurture)")
        return plan

    fresh = E.hint_get_membership(mem_id)
    if fresh is None:
        plan.update(action="exit", reason="membership canceled/deleted")
        _mark(state, mem_id, m, status="stopped", reason="canceled_or_deleted")
        return plan
    if (fresh.get("status") or "") not in ("pending", ""):
        plan.update(action="exit", reason=f"membership status={fresh.get('status')} (active/converted)")
        _mark(state, mem_id, m, status="suppressed", reason=f"status_{fresh.get('status')}")
        return plan

    our_pt = E.hint_get_patient(pat_id) or {}
    first_name, phone = E.first_name_and_phone(our_pt)
    phone_e164 = E.normalize_phone_e164(phone)
    emails, phones = E.patient_emails_phones(our_pt)
    our_family = E.plan_family(m["plan"])

    matched = E.match_records(all_patients, emails, phones)
    our, other = conversion_scan(matched, pat_id, our_family)
    other_any = other["card"] or other["active"] or other["nonpend_fam"]
    our_separate_membership = our["active"] or our["nonpend_fam"]
    conversion_detected = our["card"] or our_separate_membership or other_any
    contact = E.spruce_contact_for_patient(contacts, pat_id, phone_e164)

    if conversion_detected:
        should_cancel = other_any or our_separate_membership
        already = state.get(mem_id, {}).get("reconciled")
        if should_cancel and (fresh.get("status") == "pending") and not already:
            guards = {"other_record_signal": other_any, "our_separate_membership": our_separate_membership}
            E.hint_cancel_membership(mem_id, reason="link-conversion reconcile (duplicate)")
            E.audit("reconcile_cancel", pat_id, mem_id, "duplicate link-conversion detected", guards)
            _mark(state, mem_id, m, status="suppressed", reason="reconciled_duplicate_conversion")
            state[mem_id]["reconciled"] = True
            plan.update(action="reconcile_cancel",
                        reason="duplicate conversion -> canceled stale pending membership (once)")
            return plan
        plan.update(action="exit", reason="cross-record conversion (payment/active elsewhere)")
        _mark(state, mem_id, m, status="suppressed", reason="cross_record_conversion")
        return plan

    if contact and E.spruce_thread_has_opt_out(contact.get("id")):
        plan.update(action="exit", reason="inbound STOP/opt-out in Spruce thread")
        _mark(state, mem_id, m, status="stopped", reason="opt_out")
        return plan

    if not phone_e164:
        plan.update(action="skip", reason="no sendable phone")
        return plan

    is_new = mem_id not in state or state[mem_id].get("status") == "pending_approval"
    created = m.get("created_at")
    is_preexisting = (go_live_at is None) or (created and created < go_live_at)
    if is_new and is_preexisting and (mem_id not in approved):
        plan.update(action="needs_approval",
                    reason="pre-existing pending membership; awaiting Charlie approval (halt cond. 2)")
        _mark(state, mem_id, m, status="pending_approval", reason="preexisting_back_scan")
        return plan

    enrolled_at = _ensure_enrolled(state, mem_id, m, today)
    touches_sent = state[mem_id].get("touches_sent", [])
    plan["schedule"] = {f"day{d}": (datetime.fromisoformat(enrolled_at) + timedelta(days=d)).date().isoformat()
                        for d in E.SEQUENCE_DAYS}

    day = E.due_touch(enrolled_at, touches_sent)
    if day is None:
        return _handle_complete_or_wait(plan, m, state, mem_id, pat_id, matched, our, other,
                                        contact, enrolled_at, touches_sent, name_red)

    if E.review_asked_today(pat_id):
        plan.update(action="defer", day=day, reason="review poller asked this patient today; defer 1 day")
        return plan

    url = E.signup_url_for(m["plan_id"])
    body = E.render(day, first_name, url=url)
    ok = E.spruce_send_sms(phone_e164, body)
    if ok and not E.DRY_RUN:
        touches_sent = sorted(set(touches_sent + [day]))
        state[mem_id]["touches_sent"] = touches_sent
        state[mem_id]["last_touch_at"] = today.isoformat()
        state[mem_id]["status"] = ("completed" if set(touches_sent) >= set(E.SEQUENCE_DAYS) else "active")
    plan.update(action=("would_send" if E.DRY_RUN else ("sent" if ok else "send_failed")),
                day=day, reason=f"Day {day} touch{' (with link)' if (day == 0 and url) else ''}",
                body_preview=body)
    return plan


def _handle_complete_or_wait(plan, m, state, mem_id, pat_id, matched, our, other, contact,
                             enrolled_at, touches_sent, name_red):
    if set(touches_sent) < set(E.SEQUENCE_DAYS):
        nxt = next((d for d in E.SEQUENCE_DAYS if d not in touches_sent), None)
        plan.update(action="wait", reason=f"next touch Day {nxt} on {plan['schedule'].get(f'day{nxt}')}")
        return plan
    state[mem_id]["status"] = "completed"

    if E.start_date_passed(m.get("start_date")) and not state[mem_id].get("staff_flagged"):
        state[mem_id]["staff_flagged"] = True
        log.warning(f"STAFF FLAG: {mem_id} ({name_red}) finished Day 21, still pending past "
                    f"start_date {m.get('start_date')}, no payment.")

    if E.days_since(enrolled_at) < E.CLEANUP_DAY:
        plan.update(action="completed", reason=f"all touches sent; cleanup at Day {E.CLEANUP_DAY}")
        return plan
    if state[mem_id].get("cleaned"):
        plan.update(action="completed", reason="cleanup already done")
        return plan

    no_payment = not (our["card"] or other["card"])
    no_active = not (our["active"] or other["active"])
    fut = E.hint_has_future_appointment(pat_id)
    no_future_appt = (fut is False)
    inbound = E.spruce_thread_has_any_inbound(contact.get("id")) if contact else False
    no_inbound = (inbound is False)
    guards = {"no_payment": no_payment, "no_active_membership": no_active,
              "no_future_appointment": no_future_appt, "no_inbound_reply": no_inbound}

    if all(guards.values()):
        E.hint_cancel_membership(mem_id, reason="Day-30 end-of-sequence cleanup (no conversion)")
        E.archive_queue_add(pat_id, name_red, mem_id, "Day-30 cleanup: non-converter, all guards passed")
        E.audit("day30_cleanup", pat_id, mem_id, "end-of-sequence cleanup", guards)
        state[mem_id]["cleaned"] = True
        state[mem_id]["status"] = "cleaned"
        plan.update(action="cleanup", reason="Day-30: canceled stale pending membership + queued for staff archive")
    else:
        blocked = [k for k, v in guards.items() if not v]
        plan.update(action="cleanup_blocked", reason=f"Day-30 cleanup blocked by: {', '.join(blocked)}")
    return plan


def _ensure_enrolled(state, mem_id, m, today):
    rec = state.setdefault(mem_id, {})
    if not rec.get("enrolled_at"):
        rec.update(pat_id=m["pat_id"], plan=m["plan"], plan_id=m["plan_id"], start_date=m["start_date"],
                   created_at=m["created_at"], enrolled_at=today.isoformat(), touches_sent=[], status="active")
    return rec["enrolled_at"]


def _mark(state, mem_id, m, status, reason):
    rec = state.setdefault(mem_id, {})
    rec.update(pat_id=m["pat_id"], plan=m["plan"], start_date=m["start_date"],
               created_at=m["created_at"], status=status, reason=reason)


def main():
    args = parse_args()
    if args.dry_run:
        E.DRY_RUN = True
    if not E.HINT_API_KEY:
        log.error("HINT_API_KEY not set. Aborting.")
        sys.exit(2)
    if args.list_pending:
        list_pending()
        sys.exit(0)

    today = datetime.now(timezone.utc)
    log.info(f"nurture poller start (T5d): dry_run={E.DRY_RUN} date={today.date()}")

    state = E.read_nurture_state()
    if args.go_live and not state["_meta"].get("go_live_at"):
        state["_meta"]["go_live_at"] = today.isoformat()
        log.info(f"go_live_at stamped: {today.isoformat()}")
    go_live_at = state["_meta"].get("go_live_at")
    approved = load_approved()

    pending = E.hint_list_pending_memberships()
    all_patients = E.hint_all_patients()
    contacts = E.spruce_list_contacts()
    log.info(f"nurture poller: {len(pending)} pending; {len(all_patients)} patients scanned; "
             f"go_live_at={go_live_at} approved={len(approved)}")

    plans = []
    for m in sorted(pending, key=lambda x: x.get("mem_id") or ""):
        try:
            plans.append(evaluate(m, all_patients, contacts, state, approved, go_live_at, today))
        except Exception as e:
            log.error(f"error evaluating {m.get('mem_id')}: {e}")
            plans.append({"mem": m.get("mem_id"), "action": "error", "reason": str(e)})

    if not E.DRY_RUN:
        E.write_nurture_state(state)

    print("\n================ NURTURE RUN PLAN (T5d) ================")
    print(f"date={today.date()}  dry_run={E.DRY_RUN}  pending={len(pending)}  go_live_at={go_live_at}")
    if not plans:
        print("No pending memberships. Nothing to do.")
    for p in plans:
        line = f"- [{p['action']}] {p.get('name','?')} ({p['mem']})"
        if p.get("plan"):
            line += f" plan={p['plan']}"
        if p.get("day") is not None:
            line += f" Day {p['day']}"
        if p.get("reason"):
            line += f" -- {p['reason']}"
        print(line)
        if p.get("body_preview"):
            print(f"    body: {p['body_preview']}")
    needs = [p for p in plans if p["action"] == "needs_approval"]
    if needs:
        print(f"\n** {len(needs)} pre-existing pending membership(s) AWAITING APPROVAL (not sent). **")
    print("=======================================================\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
