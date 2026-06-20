#!/usr/bin/env python3
"""Offline unit checks for the nurture engine pure logic. No network, no sends."""
from datetime import datetime, timezone, timedelta
import nurture_engine as E

fails = []

def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        fails.append(name)

# 1. Verbatim copy + token substitution, and Day 21 STOP language present.
d0 = E.render(0, "Charlie")
d7 = E.render(7, "Charlie")
d21 = E.render(21, "Charlie")
check("Day0 starts correctly", d0.startswith("Hi Charlie, this is James from Mt. Baker Medical."))
check("Day0 has signup link", E.SIGNUP_LINK in d0)
check("Day7 'James here'", "James here, just checking in." in d7)
check("Day21 verbatim STOP", d21.endswith("It was a real pleasure meeting you. Reply STOP to opt out."))
check("link is concierge signup", E.SIGNUP_LINK == "https://mtbakermedical.hint.com/signup/concierge")
check("fallback name 'there'", E.render(0, "").startswith("Hi there,"))

# 2. E.164 normalization (copied helper parity).
check("10-digit -> +1", E.normalize_phone_e164("(360) 349-8094") == "+13603498094")
check("11-digit -> +", E.normalize_phone_e164("1-360-349-8094") == "+13603498094")
check("empty -> ''", E.normalize_phone_e164("") == "")

# 3. Cadence: due_touch returns the right day given elapsed days + sent set.
today = datetime.now(timezone.utc)
def iso_days_ago(n):
    return (today - timedelta(days=n)).isoformat()
check("Day0 due at enroll", E.due_touch(iso_days_ago(0), []) == 0)
check("Day0 not re-sent", E.due_touch(iso_days_ago(2), [0]) is None)
check("Day7 due at day 8", E.due_touch(iso_days_ago(8), [0]) == 7)
check("Day21 due at day 25", E.due_touch(iso_days_ago(25), [0, 7]) == 21)
check("nothing due after all sent", E.due_touch(iso_days_ago(30), [0, 7, 21]) is None)
check("catch-up picks latest due", E.due_touch(iso_days_ago(25), []) == 21)

# 4. Opt-out keyword detection logic (mirror of the inbound check).
def is_optout(txt):
    t = txt.strip().lower()
    first = t.split()[0] if t.split() else ""
    return t in E.OPT_OUT_KEYWORDS or first in E.OPT_OUT_KEYWORDS
check("STOP detected", is_optout("STOP"))
check("'stop please' detected", is_optout("stop please"))
check("UNSUBSCRIBE detected", is_optout("Unsubscribe"))
check("normal reply not opt-out", not is_optout("Thanks, sounds great!"))

# 5. Denylist catches the dummy patterns.
check("denylist catches ZZ-TEST", E.is_denylisted({"id": "x", "displayName": "ZZ-TEST"}))
check("denylist catches NurtureQA", E.is_denylisted({"id": "x", "displayName": "NurtureQA-DoNotContact"}))
check("real name not denylisted", not E.is_denylisted({"id": "x", "displayName": "Jane Doe"}))

print("\nTOTAL:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
