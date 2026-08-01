#!/usr/bin/env python3
"""Dashboard alert upgrade (2026-07-29):
  - deep-link the unpaid alert to Hint's past-due-invoices view
  - widen it from Concierge-only to ALL unpaid memberships (adds standing-order)
  - fix the wrong copy ("not being billed" -> bills went out and were not paid)
  - add a new alert for Hint status "unconfirmed" (online signup awaiting confirm)

Run once from mbm-review-receiver:  py _patch_dash_alerts_20260729.py
Count-asserted; aborts before writing on any mismatch. Makes a backup.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "dashboard_index.html"

R = []


def rep(old, new, n=1, label=""):
    R.append((old, new, n, label))


# --- 1. Hint deep-link constants ---------------------------------------------
rep(
    'const DATA_VERSION = "2026-07-29f";',
    '''const DATA_VERSION = "2026-07-29g";
/* Hint deep links. NO patient ids ever appear in this file - these are list views
   only, so the hosted copy stays free of PHI (Cloudflare is not BAA-covered).
   past-due view is a SUPERSET of unpaid memberships: it lists every past-due
   customer invoice, including one-off charges (labs, procedures) on members whose
   membership status is still active. Confirmed working 2026-07-29 (Charlie). */
const HINT_PAST_DUE_URL = "https://app.hint.com/patients?filter=past_due_customer_invoices&patients_sort=last_name";
const HINT_PATIENTS_URL = "https://app.hint.com/patients";''',
    1, "DATA_VERSION bump + Hint deep-link constants",
)

# --- 2. carry unpaid_so + unconfirmed into the snapshot ----------------------
rep(
    """    active_recon: {as_of:"2026-07-29T17:55Z", unpaid_concierge:2, pending_concierge:2,
                   comp_active:11, test_active_excluded:1},""",
    """    active_recon: {as_of:"2026-07-29T17:55Z", unpaid_concierge:2, pending_concierge:2,
                   unpaid_so:1, unconfirmed_total:0,
                   comp_active:11, test_active_excluded:1},""",
    1, "active_recon gains unpaid_so + unconfirmed_total",
)

# --- 3. the alert itself ------------------------------------------------------
OLD_ALERT = """  /* 3b. Unpaid concierge membership: live in Hint, not paying — real revenue leak */
  const _rec=SNAPSHOT.members.active_recon;
  if(_rec && +_rec.unpaid_concierge>0)
    alerts.push('<div class="alert amber">\U0001f4b3<div><b>'+fmtN(_rec.unpaid_concierge)+' Concierge membership'+(_rec.unpaid_concierge>1?'s are':' is')+' status "unpaid" in Hint</b> — an active patient not being billed (~$300/mo each). Not counted in the North Star. <a href="https://app.hint.com/members" target="_blank" rel="noopener" style="color:#7a4e00;font-weight:800;text-decoration:underline">→ Open Hint members &amp; add a payment method</a>. As of '+escapeHtml(_rec.as_of||'')+'.</div></div>');"""

NEW_ALERT = """  /* 3b. Unpaid memberships. Per Hint's own docs, status "unpaid" means the membership
     HAS bills (or invoices, if sponsored) that went out and were not paid - a
     COLLECTIONS problem, not a billing-setup problem. Unpaid != active, so these are
     already excluded from the North Star. Covers Concierge AND standing-order: the
     2026-07-29 case that prompted this was a semaglutide patient with an expired card
     who was invisible because the old alert only watched Concierge. */
  const _rec=SNAPSHOT.members.active_recon;
  const _unpaidC=+((_rec&&_rec.unpaid_concierge)||0), _unpaidS=+((_rec&&_rec.unpaid_so)||0);
  const _unpaidTot=_unpaidC+_unpaidS;
  if(_rec && _unpaidTot>0){
    const _bits=[];
    if(_unpaidC>0) _bits.push(fmtN(_unpaidC)+' Concierge');
    if(_unpaidS>0) _bits.push(fmtN(_unpaidS)+' standing-order');
    alerts.push('<div class="alert amber">\U0001f4b3<div><b>'+fmtN(_unpaidTot)+' membership'+(_unpaidTot>1?'s are':' is')+' status "unpaid" in Hint</b> ('+_bits.join(' + ')+') — bills went out and were not paid. Already excluded from the North Star. <a href="'+HINT_PAST_DUE_URL+'" target="_blank" rel="noopener" style="color:#7a4e00;font-weight:800;text-decoration:underline">→ Open Hint · past-due invoices</a> — use each patient\\'s <i>Send Request For Payment Info</i> button rather than handling card data yourself. Heads up: that view lists every past-due <i>invoice</i>, a superset — one-off charges (labs, procedures) show there without the membership being unpaid. As of '+escapeHtml(_rec.as_of||'')+'.</div></div>');
  }
  /* 3c. Unconfirmed signups. Hint's 5th status: the patient enrolled through an online
     signup link and is waiting on a confirm click (unless auto-confirm is on). Not
     active, not counted, not billed - a real member the dashboard cannot see, which is
     the same failure mode as the couples undercount. Zero today; alert so it stays that way. */
  if(_rec && +_rec.unconfirmed_total>0)
    alerts.push('<div class="alert red">\U0001f6a8<div><b>'+fmtN(_rec.unconfirmed_total)+' membership'+(+_rec.unconfirmed_total>1?'s are':' is')+' status "unconfirmed" in Hint</b> — someone signed up online and is waiting on you to confirm them. Not counted, not being billed. <a href="'+HINT_PATIENTS_URL+'" target="_blank" rel="noopener" style="color:#7a1a1a;font-weight:800;text-decoration:underline">→ Open Hint patients</a>. As of '+escapeHtml(_rec.as_of||'')+'.</div></div>');"""

rep(OLD_ALERT, NEW_ALERT, 1, "unpaid alert widened + deep-linked; unconfirmed alert added")

rep('  baked_at: "2026-07-29T18:05Z",',
    '  baked_at: "2026-07-29T18:40Z",', 1, "baked_at")


def main():
    if not TARGET.exists():
        print(f"ABORT: {TARGET} not found", file=sys.stderr)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if "HINT_PAST_DUE_URL" in s:
        print("ABORT: already patched. Nothing to do.")
        return 1

    for old, new, want, label in R:
        got = s.count(old)
        if got != want:
            print(f"ABORT: '{label}' matched {got} time(s), expected {want}. "
                  f"File not modified.", file=sys.stderr)
            return 3

    for old, new, want, label in R:
        s = s.replace(old, new, want)
        print(f"  ok  {label}")

    bak = TARGET.with_name("dashboard_index.html.bak-20260729-alerts")
    bak.write_text(raw, encoding="utf-8", newline="")
    out = s.replace("\n", "\r\n") if crlf else s
    TARGET.write_text(out, encoding="utf-8", newline="")

    print(f"\nPATCHED {TARGET.name}  ({len(out)} bytes)")
    print(f"backup  {bak.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
