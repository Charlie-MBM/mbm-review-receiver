#!/usr/bin/env python3
"""Abstractive Health API client - Mt. Baker Medical.

Pulls outside records + AI longitudinal summary for a patient via Carequality
and saves them into the patient's folder for filing/pasting.

SAFETY GATE: patient operations refuse to run until ABSTRACTIVE_ENABLED=true
in .env. --ping (auth test) is always allowed - it touches no patient data.

Usage:
  py abstractive_client.py --ping
  py abstractive_client.py search --first Jane --last Doe --dob 19551231 --gender F \
      [--street "1 Main St" --city Bellingham --state WA --zip 98225] \
      [--no-zip] [--robustness Optimized] [--test]

API facts (verified against the reference docs + live calls 2026-08-03):
  base https://api.abstractive.ai; get-token good for 60 min.
  search-patient body: patient_metadata[0].demographics.{given_name, family_name,
  administrative_gender_code, birth_time YYYYMMDD}; addresses[] is optional but
  ALL FOUR of street/city/state/zip must be present or the call 400s; country
  is "USA". Same patient max once per 5 min. Summary poll every 15s+, max 30.
  Presigned download URLs expire in 3600s.

run_search() is the shared entry point - abstractive_prefetch.py calls it too.
Fix API behaviour HERE and both paths get it.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.abstractive.ai"
ENV_PATH = Path(__file__).parent / ".env"
DEFAULT_OUT_ROOT = Path.home() / "MBM_Abstractive_Out"


def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"')
    return env


def out_root(env):
    """Where patient folders are written. Set ABSTRACTIVE_OUT_DIR in .env to the
    synced Google Drive path so files land in the patient's Drive folder."""
    return Path(env.get("ABSTRACTIVE_OUT_DIR") or DEFAULT_OUT_ROOT)


def post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"raw": e.read().decode(errors="replace")[:500]}


def get_token(env):
    code, r = post("/get-token", {
        "user_api_email": env["ABSTRACTIVE_API_EMAIL"],
        "user_api_password": env["ABSTRACTIVE_API_PASSWORD"],
        "username_api": env["ABSTRACTIVE_API_EMAIL"],
    })
    if code != 200 or not r.get("access_token"):
        sys.exit(f"AUTH FAILED ({code}): {r.get('failure_reason') or r}")
    return r["access_token"]


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def cmd_ping(env):
    tok = get_token(env)
    print(f"PING OK - token received (length {len(tok)}). API is live for this account.")


def folder_name(demo):
    """Drive convention: "LAST, First - YYYY" (birth year)."""
    return f"{demo['last'].strip().upper()}, {demo['first'].strip()} - {demo['dob'][:4]}"


def run_search(env, demo, root=None, want_zip=True, robustness="Optimized",
               test=False, token=None, log=print):
    """Search the HIE for one patient, download summary (+ZIP), return a result dict.

    demo: {first, last, dob YYYYMMDD, gender M/F, street, city, state, zip}
    Never raises for an API-level failure - returns {"ok": False, "error": ...} so a
    batch caller can carry on to the next patient.
    """
    if env.get("ABSTRACTIVE_ENABLED", "false").lower() != "true":
        return {"ok": False, "error": "ABSTRACTIVE_ENABLED is not true"}
    if "<" in demo["first"] or "<" in demo["last"] or not re.fullmatch(r"\d{8}", demo["dob"] or ""):
        return {"ok": False, "error": "placeholder or malformed demographics"}

    root = Path(root) if root else out_root(env)
    tok = token or get_token(env)
    email = env["ABSTRACTIVE_API_EMAIL"]

    patient = {"demographics": {
        "given_name": demo["first"], "family_name": demo["last"],
        "administrative_gender_code": demo["gender"].upper(), "birth_time": demo["dob"],
    }}
    parts = {"street_address_line": demo.get("street"), "city": demo.get("city"),
             "state": demo.get("state"), "postal_code": demo.get("zip")}
    if all(parts.values()):
        parts["country"] = "USA"
        patient["addresses"] = [parts]
    elif any(parts.values()):
        log("NOTE: partial address ignored - the API needs street, city, state and "
            "zip together. Searching on name + DOB only; matching may be weaker.")

    body = {"user_api_email": email, "token": tok, "patient_metadata": [patient],
            "robustness": robustness, "summarize": True}
    if test:
        body["test"] = True

    code, r = post("/search-patient", body)
    log(f"search-patient -> {code} {r.get('status')}")
    if code == 429:
        return {"ok": False, "error": "rate limited (same patient once per 5 min)"}
    if code not in (200, 202):
        return {"ok": False, "error": f"search failed: {r}"}

    conv = r["conversation_id"]
    pid = r["results"][0]["patient_id"]
    log(f"conversation {conv} / patient {pid} - waiting for summary "
        f"(typically 1-8 min depending on robustness)...")

    out_dir = root / folder_name(demo)
    stamp = time.strftime("%Y-%m-%d")
    result = {"ok": True, "conversation_id": conv, "out_dir": out_dir,
              "summary": None, "zip": None, "error": None}

    for attempt in range(30):
        time.sleep(20)
        code, r = post("/retrieve-summary", {
            "user_api_email": email, "token": tok,
            "conversation_id": conv, "patient_id": pid})
        if code == 200 and r.get("url"):
            result["summary"] = download(
                r["url"], out_dir / f"{stamp} - outside-records - Abstractive summary.json")
            log(f"summary saved: {result['summary']}")
            break
        log(f"  poll {attempt + 1}: still processing ({code})")
    else:
        result["error"] = "summary not ready after 30 polls (search continues server-side)"
        log(result["error"])

    if want_zip:
        code, r = post("/retrieve-patient-docs", {
            "user_api_email": email, "token": tok,
            "conversation_id": conv, "patient_id": pid})
        url = (r.get("results") or [{}])[0].get("url") if code == 200 else None
        if url:
            result["zip"] = download(
                url, out_dir / f"{stamp} - outside-records - full docs.zip")
            log(f"full document ZIP saved: {result['zip']}")
        else:
            log(f"docs not ready ({code}) - re-run later if needed.")

    return result


def cmd_search(env, a):
    if env.get("ABSTRACTIVE_ENABLED", "false").lower() != "true":
        sys.exit("BLOCKED: ABSTRACTIVE_ENABLED is not true. Patient searches wait "
                 "for the countersigned BAA. Flip the flag in .env once it's back.")
    if "<" in a.first or "<" in a.last or not re.fullmatch(r"\d{8}", a.dob or ""):
        sys.exit("ABORT: placeholder values detected. --first/--last need the real "
                 "name and --dob must be 8 digits YYYYMMDD (e.g. 19551231).")
    demo = {"first": a.first, "last": a.last, "dob": a.dob, "gender": a.gender,
            "street": a.street, "city": a.city, "state": a.state, "zip": a.zip}
    res = run_search(env, demo, want_zip=not a.no_zip,
                     robustness=a.robustness, test=a.test)
    if not res["ok"]:
        sys.exit(res["error"])
    print(f"\nDone -> {res['out_dir']}")
    print("Next: py abstractive_render.py   (writes the paste-ready .md)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--ping", action="store_true", help="auth test, no patient data")
    s = sub.add_parser("search")
    s.add_argument("--first", required=True)
    s.add_argument("--last", required=True)
    s.add_argument("--dob", required=True, help="YYYYMMDD")
    s.add_argument("--gender", required=True, choices=list("MFmf"))
    s.add_argument("--street"); s.add_argument("--city"); s.add_argument("--state")
    s.add_argument("--zip"); s.add_argument("--robustness", default="Optimized")
    s.add_argument("--no-zip", action="store_true",
                   help="summary only, skip the ~45MB document ZIP")
    s.add_argument("--test", action="store_true", help="API test mode request")
    a = ap.parse_args()
    env = load_env()
    for k in ("ABSTRACTIVE_API_EMAIL", "ABSTRACTIVE_API_PASSWORD"):
        if not env.get(k):
            sys.exit(f"missing {k} in .env")
    if a.ping:
        cmd_ping(env)
    elif a.cmd == "search":
        cmd_search(env, a)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
