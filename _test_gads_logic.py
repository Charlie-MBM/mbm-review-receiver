#!/usr/bin/env python3
"""Regression tests for the Google Ads / organic Google attribution split.

PHI-FREE. Every input below is a synthetic dict literal written by hand. This
script makes NO network calls and reads NO patient data -- it imports the real
functions out of the real files and exercises them on invented inputs. Safe to
run any time; safe to paste the output back into chat.

WHY THIS EXISTS instead of a live test booking: proving the split end-to-end for
real means putting a booking through /book-beta with a ?gclid=, which sends a
real Spruce SMS and occupies a real 30-minute slot on Dr. Scribner's calendar
until someone deletes it. That is a fine thing to do on purpose in the morning;
it is not a fine thing to do unattended overnight. This gets the logic to
"proven" without touching the calendar. The live smoke test is still worth doing
once -- it is the only thing that proves the WIRING (Worker -> GCal -> poller ->
Hint), as opposed to the RULES, which is what this file covers.

FOUR LAYERS ARE CHECKED:
  1. poller  derive_lead_source()        -- no self-report, derive from metadata
  2. poller  resolve_gcal_lead_source()  -- self-report, possibly refined
  3. exporter map_source()               -- label -> dashboard bucket
  4. CROSS-FILE PARITY (the part that has actually bitten us, twice):
       - the Worker's PAID_MEDIUMS/PAID_SOURCES vs the poller's
       - the Worker's FALLBACK_CATALOG vs the poller's _LEAD_SOURCE_FALLBACK
       - every label the code can emit exists in the catalog
       - every SOURCE_KEYS bucket is actually rendered by the dashboard

Layer 4 is the important one. `nextdoor` was in SOURCE_KEYS but in none of the
three hardcoded dashboard lists, so Nextdoor members silently vanished from the
channel bar; the same class of omission then turned up a second time in both
fallback catalogs. A list that must be updated in four places will drift again.
This turns that drift into a failing test instead of a number nobody notices.

Run from mbm-review-receiver:  py _test_gads_logic.py
Exit 0 = all green. Exit 1 = at least one failure (details printed).
"""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTER_PY = HERE / "export_dashboard_members.py"
BAKE_PY = HERE / "bake_dashboard.py"
HTML = HERE / "dashboard_index.html"
POLLER_PY = HERE.parent / "mbm-hint-enrollment" / "webhook" / "send_consult_intake.py"
WORKER_API_TS = HERE.parent / "mbm-rebuild-43f1acd5" / "src" / "book" / "api.ts"
WORKER_HINT_TS = HERE.parent / "mbm-rebuild-43f1acd5" / "src" / "book" / "hint.ts"

FAILURES = []
CHECKS = 0


def check(label, got, want):
    global CHECKS
    CHECKS += 1
    if got == want:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")
        FAILURES.append(label)


def load(path, name):
    """Import a module by path. Both target modules only read os.environ at import
    time -- no network, no file writes -- and their real work is behind
    `if __name__ == '__main__'`, so this is side-effect free."""
    if not path.exists():
        print(f"ABORT: {path} not found", file=sys.stderr)
        raise SystemExit(2)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
print("loading modules...")
poller = load(POLLER_PY, "_t_poller")
exporter = load(EXPORTER_PY, "_t_exporter")
derive = poller.derive_lead_source
resolve = poller.resolve_gcal_lead_source
paid = poller._has_paid_google_signal
bucket = exporter.map_source

GCLID = {"gclid": "Cj0KCQjw_TEST_SYNTHETIC_NOT_REAL"}
GBRAID = {"gbraid": "0AAAAA_TEST_SYNTHETIC"}
WBRAID = {"wbraid": "Cj0AAAA_TEST_SYNTHETIC"}

# ------------------------------------------------- 1. paid-signal detection --
print("\n[1] _has_paid_google_signal")
check("gclid present", paid(GCLID), True)
check("gbraid present", paid(GBRAID), True)
check("wbraid present", paid(WBRAID), True)
check("utm_medium=cpc", paid({"utm_medium": "cpc"}), True)
check("utm_medium=CPC (case)", paid({"utm_medium": "CPC"}), True)
check("utm_medium= paid_search (whitespace)", paid({"utm_medium": " paid_search "}), True)
check("utm_source=google_ads", paid({"utm_source": "google_ads"}), True)
check("utm_source=adwords", paid({"utm_source": "adwords"}), True)
check("utm_medium=organic", paid({"utm_medium": "organic"}), False)
check("utm_medium=referral", paid({"utm_medium": "referral"}), False)
check("empty gclid string", paid({"gclid": "   "}), False)
check("empty dict", paid({}), False)
check("not a dict", paid(None), False)
# fbclid must NOT read as paid Google -- it is Meta, and it routes to Social media
check("fbclid only is not paid Google", paid({"fbclid": "abc123"}), False)

# ------------------------------------------------------ 2. derive (no chip) --
print("\n[2] derive_lead_source  (patient tapped no chip)")
check("gclid -> Google Ads", derive(GCLID), "Google Ads")
check("gbraid -> Google Ads", derive(GBRAID), "Google Ads")
check("wbraid -> Google Ads", derive(WBRAID), "Google Ads")
check("utm_medium=cpc -> Google Ads", derive({"utm_medium": "cpc"}), "Google Ads")
check("organic google referrer -> Google",
      derive({"referrer": "https://www.google.com/"}), "Google")
check("utm_source=google&medium=organic -> Google",
      derive({"utm_source": "google", "utm_medium": "organic"}), "Google")
check("gbp campaign -> Google", derive({"utm_campaign": "gbp"}), "Google")
check("gemini is AI not Google",
      derive({"referrer": "https://gemini.google.com/app"}), "AI")
check("chatgpt -> AI", derive({"referrer": "https://chatgpt.com/"}), "AI")
check("nextdoor -> Nextdoor", derive({"referrer": "https://nextdoor.com/feed"}), "Nextdoor")
check("fbclid -> Social media", derive({"fbclid": "abc123"}), "Social media")
check("bing referrer -> Bing", derive({"referrer": "https://www.bing.com/search"}), "Bing")
check("no signal at all -> None (never fabricate)", derive({}), None)
check("staff booking (ids stripped upstream) -> None", derive({"channel": "staff-phone"}), None)

# PARITY: the refine path and the derive path must agree on what "paid" means.
# If they disagree, the same booking is classified differently depending only on
# whether the patient happened to tap a chip -- and the two writers (Worker on
# Confirm, poller on the next pass) can then clobber each other.
print("\n[2b] derive/refine parity on paid detection")
for name, priv in [("gclid", GCLID), ("gbraid", GBRAID), ("wbraid", WBRAID),
                   ("utm_medium=cpc", {"utm_medium": "cpc"}),
                   ("utm_medium=ppc", {"utm_medium": "ppc"}),
                   ("utm_source=google_ads", {"utm_source": "google_ads"}),
                   ("utm_source=googleads", {"utm_source": "googleads"}),
                   ("utm_source=adwords", {"utm_source": "adwords"})]:
    check(f"{name}: paid signal implies derive says Google Ads",
          (paid(priv), derive(priv)), (True, "Google Ads"))

# ---------------------------------------------------- 3. resolve (with chip) --
print("\n[3] resolve_gcal_lead_source  (patient tapped a chip)")
check("Google + gclid -> refined to Google Ads",
      resolve(dict(GCLID, lead_source="Google")), ("Google Ads", True))
check("Google + gbraid -> refined",
      resolve(dict(GBRAID, lead_source="Google")), ("Google Ads", True))
check("google (lowercase) + gclid -> refined",
      resolve(dict(GCLID, lead_source="google")), ("Google Ads", True))
check("' Google ' (whitespace) + gclid -> refined",
      resolve(dict(GCLID, lead_source=" Google ")), ("Google Ads", True))
check("Google, no paid signal -> stays organic Google",
      resolve({"lead_source": "Google"}), ("Google", True))
check("Google + utm_medium=organic -> stays organic",
      resolve({"lead_source": "Google", "utm_medium": "organic"}), ("Google", True))
# The rule Charlie locked: only "Google" is ever refined. Everything else the
# patient says is information the machine does not have, and someone can hear
# about us from a friend AND arrive through an ad.
check("Word of mouth + gclid -> self-report wins outright",
      resolve(dict(GCLID, lead_source="Word of mouth")), ("Word of mouth", True))
check("Provider/ER Referral + gclid -> self-report wins",
      resolve(dict(GCLID, lead_source="Provider/ER Referral")), ("Provider/ER Referral", True))
check("Nextdoor + gclid -> self-report wins",
      resolve(dict(GCLID, lead_source="Nextdoor")), ("Nextdoor", True))
check("Bing + gclid -> self-report wins (weird but honest)",
      resolve(dict(GCLID, lead_source="Bing")), ("Bing", True))
check("no chip + gclid -> derived Google Ads, is_self=False",
      resolve(dict(GCLID)), ("Google Ads", False))
check("no chip, no signal -> (None, False)", resolve({}), (None, False))
check("blank chip string is treated as no chip",
      resolve({"lead_source": "   "}), (None, False))

# ------------------------------------------------------- 4. dashboard bucket --
print("\n[4] map_source  (label -> dashboard bucket)")
check("Google Ads -> google_ads", bucket("Google Ads"), "google_ads")
check("google ads (case) -> google_ads", bucket("google ads"), "google_ads")
check("AdWords -> google_ads", bucket("AdWords"), "google_ads")
check("Google -> google (NOT google_ads)", bucket("Google"), "google")
check("Google Local Services -> google_lsa", bucket("Google Local Services"), "google_lsa")
check("Bing -> bing", bucket("Bing"), "bing")
check("AI -> ai", bucket("AI"), "ai")
check("Social media -> social", bucket("Social media"), "social")
check("Nextdoor -> nextdoor", bucket("Nextdoor"), "nextdoor")
check("Provider/ER Referral -> provider_referral",
      bucket("Provider/ER Referral"), "provider_referral")
check("Word of mouth -> word_of_mouth", bucket("Word of mouth"), "word_of_mouth")
check("Other -> other", bucket("Other"), "other")
check("None -> other", bucket(None), "other")

# The whole chain, end to end, in one assertion per row.
print("\n[4b] full chain: priv dict -> label -> bucket")
for name, priv, want_bucket in [
    ("paid click, no chip", GCLID, "google_ads"),
    ("paid click + Google chip", dict(GCLID, lead_source="Google"), "google_ads"),
    ("organic referrer, no chip", {"referrer": "https://www.google.com/"}, "google"),
    ("Google chip, no click id", {"lead_source": "Google"}, "google"),
    ("paid click + friend referral chip",
     dict(GCLID, lead_source="Word of mouth"), "word_of_mouth"),
    ("nothing at all", {}, "other"),
]:
    label, _ = resolve(priv)
    check(f"{name} -> {want_bucket}", bucket(label), want_bucket)

# ----------------------------------------------------- 5. cross-file parity --
print("\n[5] cross-file parity  (the drift that has bitten us twice)")


def ts_string_list(text, const_name):
    """Pull the quoted strings out of a `const NAME ... = new Set([...])` or
    `const NAME ... = [...]` declaration in a .ts file."""
    m = re.search(re.escape(const_name) + r"[^=]*=\s*(?:new Set\()?\[(.*?)\]",
                  text, re.S)
    if not m:
        return None
    return [s for s in re.findall(r'"([^"]*)"', m.group(1))]


if WORKER_API_TS.exists():
    api = WORKER_API_TS.read_text(encoding="utf-8")
    check("Worker PAID_MEDIUMS == poller PAID_MEDIUMS",
          sorted(ts_string_list(api, "PAID_MEDIUMS") or []),
          sorted(poller.PAID_MEDIUMS))
    check("Worker PAID_SOURCES == poller's utm_source paid list",
          sorted(ts_string_list(api, "PAID_SOURCES") or []),
          sorted(["google_ads", "googleads", "adwords"]))
    check("Worker still computes leadSourceRefined", "leadSourceRefined" in api, True)
    check("Worker records the lead_source_refined audit flag",
          "lead_source_refined" in api, True)
    # Security invariant: "Google Ads" must NOT be accepted from the browser.
    # LEAD_SOURCES validates untrusted input; the refined value is computed
    # server-side AFTER validation. If it ever lands in that set, a crafted
    # request can self-report a paid conversion it never earned.
    lead_sources = ts_string_list(api, "LEAD_SOURCES") or []
    check('"Google Ads" is NOT browser-submittable (stays out of LEAD_SOURCES)',
          "Google Ads" in lead_sources, False)
else:
    print("  skip  api.ts not found (site repo not checked out here)")

catalog_names = [d["name"] for d in poller._LEAD_SOURCE_FALLBACK]
if WORKER_HINT_TS.exists():
    hint_ts = WORKER_HINT_TS.read_text(encoding="utf-8")
    # Scope to the FALLBACK_CATALOG array ONLY. Scanning the whole file picks up
    # the API-shape doc comments at the top of hint.ts, which contain a literal
    # {name: "Other"} and four {id: "lds-…"} placeholders -- that produced two
    # phantom failures on the first run of this harness (2026-07-30).
    block = re.search(r"const FALLBACK_CATALOG[^=]*=\s*\[(.*?)^\];",
                      hint_ts, re.S | re.M)
    if not block:
        check("FALLBACK_CATALOG block locatable in hint.ts", False, True)
        cat = ""
    else:
        cat = block.group(1)
    worker_names = re.findall(r'name:\s*"([^"]+)"', cat)
    check("Worker FALLBACK_CATALOG == poller _LEAD_SOURCE_FALLBACK",
          sorted(worker_names), sorted(catalog_names))
    worker_ids = re.findall(r'id:\s*"(lds-[^"]+)"', cat)
    check("catalog ids match too",
          sorted(worker_ids), sorted(d["id"] for d in poller._LEAD_SOURCE_FALLBACK))
else:
    print("  skip  hint.ts not found (site repo not checked out here)")

# Every label the code can emit must exist in the fallback catalog, or a
# fallback-path write silently degrades it to "Other".
EMITTABLE = ["Google", "Google Ads", "Google Local Services", "Bing", "AI",
             "Social media", "Nextdoor", "Provider/ER Referral", "Word of mouth", "Other"]
lower_catalog = [n.lower() for n in catalog_names]
for lbl in EMITTABLE:
    check(f'catalog can resolve "{lbl}"', lbl.lower() in lower_catalog, True)

# Every dashboard bucket must actually render. This is the nextdoor bug as a test.
if BAKE_PY.exists():
    bake = BAKE_PY.read_text(encoding="utf-8")
    for key in exporter.SOURCE_KEYS:
        check(f'bake_dashboard emits "{key}"', f'"{key}"' in bake, True)
if HTML.exists():
    html = HTML.read_text(encoding="utf-8")
    m = re.search(r"const order=\[(.*?)\];", html, re.S)
    rendered = re.findall(r'\["([a-z_]+)"', m.group(1)) if m else []
    check("dashboard `order` array covers every SOURCE_KEYS bucket",
          sorted(rendered), sorted(exporter.SOURCE_KEYS))

# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
if FAILURES:
    print(f"{len(FAILURES)} FAILED of {CHECKS} checks:")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print(f"ALL {CHECKS} CHECKS PASSED")
print("Rules are proven. Still unproven: the WIRING (Worker -> GCal -> poller ->")
print("Hint). That needs one live ?gclid= booking with a synthetic patient.")
raise SystemExit(0)
