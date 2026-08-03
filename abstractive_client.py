#!/usr/bin/env python3
"""Abstractive Health API client - Mt. Baker Medical (2026-08-02).

Pulls outside records + AI longitudinal summary for a patient via Carequality
and saves everything locally for filing into the patient's Drive folder.

SAFETY GATE: patient operations refuse to run until ABSTRACTIVE_ENABLED=true
in .env. Flip it ONLY after Abstractive's countersigned BAA is in hand.
--ping (auth test) is always allowed - it touches no patient data.

Usage:
  py abstractive_client.py --ping
  py abstractive_client.py search --first Jane --last Doe --dob 19551231 --gender F [--city Bellingham --state WA --zip 98225 --robustness Optimized --test]

API facts (reference docs, verified 2026-08-02): base https://api.abstractive.ai;
get-token (60 min); search-patient needs given_name/family_name/
administrative_gender_code/birth_time YYYYMMDD; same patient max once per 5 min;
summary poll every 15s+, max 30 polls; presigned URLs expire in 3600s.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://api.abstractive.ai"
ENV_PATH = Path(__file__).parent / ".env"
OUT_ROOT = Path.home() / "MBM_Abstractive_Out"


def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"')
    return env


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


def cmd_search(env, a):
    if env.get("ABSTRACTIVE_ENABLED", "false").lower() != "true":
        sys.exit("BLOCKED: ABSTRACTIVE_ENABLED is not true. Patient searches wait "
                 "for the countersigned BAA. Flip the flag in .env once it's back.")
    tok = get_token(env)
    email = env["ABSTRACTIVE_API_EMAIL"]
    if "<" in a.first or "<" in a.last or not re.fullmatch(r"\d{8}", a.dob or ""):
        sys.exit("ABORT: placeholder values detected. --first/--last need the real "
                 "name and --dob must be 8 digits YYYYMMDD (e.g. 19551231).")
    # Demographics MUST be nested under "demographics" (verified against the API
    # reference 2026-08-03); a flat payload returns 400 missing_fields. Country is
    # "USA", not "US".
    patient = {
        "demographics": {
            "given_name": a.first, "family_name": a.last,
            "administrative_gender_code": a.gender.upper(), "birth_time": a.dob,
        }
    }
    # A PARTIAL address is rejected: 400 "Missing required fields:
    # street_address_line, postal_code" (verified 2026-08-03). Send all four or
    # none. An address materially improves matching, so say so when we drop it.
    parts = {"street_address_line": a.street, "city": a.city,
             "state": a.state, "postal_code": a.zip}
    if all(parts.values()):
        parts["country"] = "USA"
        patient["addresses"] = [parts]
    elif any(parts.values()):
        print("NOTE: partial address ignored - the API needs street, city, state and "
              "zip together. Searching on name + DOB only; matching may be weaker.")
    body = {"user_api_email": email, "token": tok,
            "patient_metadata": [patient],
            "robustness": a.robustness, "summarize": True}
    if a.test:
        body["test"] = True
    code, r = post("/search-patient", body)
    print(f"search-patient -> {code} {r.get('status')}")
    if code == 429:
        sys.exit("Rate limited: same patient can only be searched once per 5 minutes.")
    if code not in (200, 202):
        sys.exit(f"search failed: {r}")
    conv = r["conversation_id"]
    pid = r["results"][0]["patient_id"]
    print(f"conversation {conv} / patient {pid} - waiting for summary "
          f"(typically 1-8 min depending on robustness)...")

    # Match the Drive folder convention: "LAST, First - YYYY" (birth year).
    # .capitalize() used to lowercase the 2nd word of two-part first names
    # ("Lou Ann" -> "Lou ann"), so take the name exactly as typed.
    out_dir = OUT_ROOT / f"{a.last.strip().upper()}, {a.first.strip()} - {a.dob[:4]}"
    stamp = time.strftime("%Y-%m-%d")
    for attempt in range(30):
        time.sleep(20)
        code, r = post("/retrieve-summary", {
            "user_api_email": email, "token": tok,
            "conversation_id": conv, "patient_id": pid})
        if code == 200 and r.get("url"):
            p = download(r["url"], out_dir / f"{stamp} - outside-records - Abstractive summary.json")
            print(f"summary saved: {p}")
            break
        print(f"  poll {attempt + 1}: still processing ({code})")
    else:
        print("summary not ready after 30 polls - re-run later; search continues server-side.")

    code, r = post("/retrieve-patient-docs", {
        "user_api_email": email, "token": tok,
        "conversation_id": conv, "patient_id": pid})
    if code == 200 and r.get("results"):
        url = r["results"][0].get("url")
        if url:
            p = download(url, out_dir / f"{stamp} - outside-records - full docs.zip")
            print(f"full document ZIP saved: {p}")
    else:
        print(f"docs not ready ({code}) - re-run later if needed.")
    print(f"\nDone. File outputs from {out_dir} into the patient's Drive folder "
          f"('MBM Patient Records'), per the folder README naming.")


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
