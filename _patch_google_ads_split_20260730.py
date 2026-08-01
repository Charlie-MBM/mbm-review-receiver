#!/usr/bin/env python3
"""Split Google Ads out from organic Google in lead-source attribution.

Charlie 2026-07-30: "is there a way to automatically distinguish between google ads
and organic google for our tracking purposes?"  Yes -- the discriminator was already
being captured and then thrown away at the last step.

WHAT WAS ALREADY TRUE (verified in code before writing this):
  * Every web booking stores gclid/gbraid/wbraid + utm_source/medium/campaign in the
    GCal event's private props. A Google click id ONLY exists on a paid ad click, so
    it is a perfect paid-vs-organic discriminator.
  * derive_lead_source() already isolates it in its FIRST branch (`if has_gclick:`)
    ... and then returns the string "Google", identical to organic. One line discarded
    the distinction.

WHAT THIS PATCH CHANGES (two files, two repos):

  mbm-review-receiver/export_dashboard_members.py
    - new "google_ads" dashboard bucket, checked BEFORE the generic "google"
      catch-all for the same reason google_lsa is: "Google Ads" contains "google"
      and would otherwise vanish into the organic bucket.

  mbm-hint-enrollment/webhook/send_consult_intake.py
    - derive_lead_source(): click id / paid utm_medium -> "Google Ads" (was "Google").
    - resolve_gcal_lead_source(): THE IMPORTANT ONE. Self-reports always overwrote
      derived values, so a patient who clicked an ad and then tapped the "Google" chip
      was written to Hint as plain "Google" and the gclid was discarded -- precisely
      the case we pay money for. A click id is now treated as a REFINEMENT of a
      "Google" self-report rather than a conflict: tap says Google + click id present
      -> "Google Ads". A self-report that says something genuinely different (Word of
      mouth, Provider referral) still wins outright -- that is real information the
      machine does not have.

NOT CHANGED, DELIBERATELY: "Google Ads" is NOT added to the patient-facing chip list.
Charlie: "i dont want it on the website where we ask patients. i doubt most would
remember if it was an ad or organic." Correct -- it is derived server-side only. Both
"Google Ads" and "Google Local Services" were created in Hint's catalog on 2026-07-30
with Show-On-Online-Signup = No for the same reason.

Run once from mbm-review-receiver:  py _patch_google_ads_split_20260730.py
Count-asserted per file; a file is only written if ALL of its replacements matched.
Backs up each file it touches.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTER = HERE / "export_dashboard_members.py"
POLLER = HERE.parent / "mbm-hint-enrollment" / "webhook" / "send_consult_intake.py"

STAMP = "20260730-gads"

# ---------------------------------------------------------------- exporter ---
EXPORTER_EDITS = [
    (
        '''SOURCE_KEYS = ["google", "google_lsa", "bing", "ai", "social", "nextdoor", "provider_referral", "word_of_mouth", "other"]''',
        '''SOURCE_KEYS = ["google", "google_ads", "google_lsa", "bing", "ai", "social", "nextdoor", "provider_referral", "word_of_mouth", "other"]''',
        1, "SOURCE_KEYS gains google_ads",
    ),
    (
        '''    if "bing" in r:
        return "bing"
    if "google" in r or "search" in r or "online" in r:
        return "google"''',
        '''    # Paid Google must ALSO be checked before the google catch-all, same trap as
    # LSA: "Google Ads" contains "google". This bucket is written by the Worker and
    # the poller from a Google click id (gclid/gbraid/wbraid) or a paid utm_medium --
    # never self-reported, because a patient cannot reliably tell an ad from an
    # organic result.
    if "google ads" in r or "google_ads" in r or "adwords" in r or "google paid" in r:
        return "google_ads"
    if "bing" in r:
        return "bing"
    if "google" in r or "search" in r or "online" in r:
        return "google"''',
        1, "map_source() google_ads branch",
    ),
]

# ------------------------------------------------------------------ poller ---
POLLER_EDITS = [
    (
        '''    if has_gclick:
        return "Google"''',
        '''    if has_gclick or utm_medium in PAID_MEDIUMS:
        # A Google click id only exists on a PAID ad click, so this is ground truth
        # for paid-vs-organic. Returning plain "Google" here (as this did until
        # 2026-07-30) collapsed paid and organic into one indistinguishable bucket.
        return "Google Ads"''',
        1, "derive_lead_source -> Google Ads",
    ),
    (
        '''    if raw:  # Worker writes lead_source only for self-reports (marker lead_source_self='1')
        return raw, True
    return derive_lead_source(priv), False''',
        '''    if raw:  # Worker writes lead_source only for self-reports (marker lead_source_self='1')
        # REFINEMENT, not override (Charlie 2026-07-30). "Google" and "Google Ads" are
        # not competing answers -- the click id is simply a more precise version of the
        # same answer, and it is the version Google Ads will match a conversion to.
        # Before this, a patient who clicked an ad and then tapped the "Google" chip was
        # recorded as plain organic Google and the gclid was thrown away.
        #
        # Only "Google" is refined. A self-report that says something genuinely
        # DIFFERENT (Word of mouth, Provider/ER referral, ...) still wins outright: that
        # is information the machine does not have, and someone can hear about us from a
        # friend and still arrive via an ad click.
        if raw.strip().lower() == "google" and _has_paid_google_signal(priv):
            return "Google Ads", True
        return raw, True
    return derive_lead_source(priv), False''',
        1, "resolve_gcal_lead_source refinement",
    ),
    (
        '''def derive_lead_source(priv):''',
        '''# utm_medium values that mean "this was a paid click". Kept narrow on purpose:
# an unrecognised medium falls through to the organic/referrer logic rather than
# being guessed as paid, because over-reporting paid inflates the channel we spend
# money on and would make ad performance look better than it is.
PAID_MEDIUMS = ("cpc", "ppc", "paid", "paidsearch", "paid_search", "ads")


def _has_paid_google_signal(priv):
    """True iff this booking carries hard evidence of a PAID Google click.

    Ground truth = a Google click id. gclid is standard; gbraid/wbraid are the
    privacy-preserving iOS/web variants Google substitutes when a gclid cannot be
    set. Any of the three means an ad was clicked. A paid utm_medium is accepted as
    a secondary signal for the case where the click id was stripped in transit.
    """
    if not isinstance(priv, dict):
        return False
    for k in ("gclid", "gbraid", "wbraid"):
        v = priv.get(k)
        if (v.strip() if isinstance(v, str) else v):
            return True
    med = priv.get("utm_medium")
    med = med.strip().lower() if isinstance(med, str) else ""
    if med in PAID_MEDIUMS:
        return True
    src = priv.get("utm_source")
    src = src.strip().lower() if isinstance(src, str) else ""
    return src in ("google_ads", "googleads", "adwords")


def derive_lead_source(priv):''',
        1, "_has_paid_google_signal helper",
    ),
]


def apply(target, edits, guard):
    if not target.exists():
        print(f"ABORT: {target} not found", file=sys.stderr)
        return 2

    raw = target.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    s = raw.replace("\r\n", "\n")

    if guard in s:
        print(f"  skip  {target.name} (already patched)")
        return 0

    for old, new, want, label in edits:
        got = s.count(old)
        if got != want:
            print(f"ABORT [{target.name}]: '{label}' matched {got} time(s), "
                  f"expected {want}. File not modified.", file=sys.stderr)
            return 3

    for old, new, want, label in edits:
        s = s.replace(old, new, want)
        print(f"  ok    {target.name}: {label}")

    bak = target.with_name(target.name + f".bak-{STAMP}")
    bak.write_text(raw, encoding="utf-8", newline="")
    target.write_text(s.replace("\n", "\r\n") if crlf else s,
                      encoding="utf-8", newline="")

    import py_compile
    py_compile.compile(str(target), doraise=True)
    print(f"  wrote {target.name}  (backup {bak.name}, syntax OK)")
    return 0


def main():
    rc = apply(EXPORTER, EXPORTER_EDITS, 'return "google_ads"')
    if rc != 0:
        return rc
    rc = apply(POLLER, POLLER_EDITS, "_has_paid_google_signal")
    if rc != 0:
        return rc

    print("\nDONE. Both files patched.")
    print("Next:  py export_dashboard_members.py   then   py bake_dashboard.py --push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
