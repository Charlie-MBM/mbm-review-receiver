#!/usr/bin/env python3
"""Weekly archive digest (LOCAL - runs on Charlie's covered-entity PC; nothing
routes through Cowork/Anthropic).

Reads the two staff-archive queues:
  - no-show ghosts:        mbm-hint-enrollment/webhook/noshow_archive_queue.csv
  - nurture non-converters: mbm-review-receiver/nurture_archive_queue.csv
and emails James + Charlie the list of records to archive by hand in Hint (Hint's
API can't archive - verified 2026-07). The email carries only the opaque patient id
+ a direct Hint deep-link per record; no names are put in the email. Each id is
emailed once (dedup via archive_digest_state.json). Sends nothing on an empty week.

Env (from mbm-review-receiver/.env):
  HINT_API_KEY, HINT_ENV            - to fetch each record's Hint deep-link
  GMAIL_IMAP_USER / _PASSWORD       - Gmail app password, used for SMTP send
  ARCHIVE_DIGEST_TO                 - comma-separated recipients (default: GMAIL_IMAP_USER)
"""
import os
import csv
import json
import ssl
import smtplib
from pathlib import Path
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv
import requests as http

HERE = Path(__file__).resolve().parent                       # mbm-review-receiver
load_dotenv(dotenv_path=HERE / ".env")

NURTURE_QUEUE = HERE / "nurture_archive_queue.csv"
NOSHOW_QUEUE = HERE.parent / "mbm-hint-enrollment" / "webhook" / "noshow_archive_queue.csv"
STATE_FILE = HERE / "archive_digest_state.json"

KEY = os.environ.get("HINT_API_KEY", "")
BASE = "https://api.hint.com" if os.environ.get("HINT_ENV") == "production" else "https://api.sandbox.hint.com"
SMTP_USER = os.environ["GMAIL_IMAP_USER"]
SMTP_PASS = os.environ["GMAIL_IMAP_PASSWORD"]
TO = [x.strip() for x in os.environ.get("ARCHIVE_DIGEST_TO", SMTP_USER).split(",") if x.strip()]


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def hint_web_link(pid):
    """Direct Hint link for a patient id (fetched locally). Falls back to '' on error."""
    if not (KEY and pid):
        return ""
    try:
        r = http.get(f"{BASE}/api/provider/patients/{pid}",
                     headers={"Authorization": f"Bearer {KEY}"}, timeout=15)
        if r.status_code == 200:
            return r.json().get("provider_web_link") or ""
    except Exception:
        pass
    return ""


def load_sent():
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def save_sent(s):
    STATE_FILE.write_text(json.dumps(sorted(s)))


def main():
    sent = load_sent()
    items = []
    for src, rows in (("nurture non-converter", read_csv(NURTURE_QUEUE)),
                      ("no-show", read_csv(NOSHOW_QUEUE))):
        for row in rows:
            pid = (row.get("patient_id") or "").strip()
            if not pid or pid in sent:
                continue
            items.append({"pid": pid, "source": src,
                          "queued_at": row.get("queued_at", ""), "reason": row.get("reason", "")})

    if not items:
        print("archive digest: nothing new to archive this week - no email sent.")
        return

    lines = [
        "These patient records are ready to archive in Hint (Hint's API can't archive,",
        "so it's a manual step). Open each link and archive the record. Each is listed once.",
        "",
    ]
    for it in sorted(items, key=lambda x: x["source"]):
        link = hint_web_link(it["pid"])
        tail = f"  ->  {link}" if link else "   (search this ID in Hint)"
        why = f"   [{it['reason']}]" if it["reason"] else ""
        lines.append(f"- ({it['source']}) {it['pid']}{tail}{why}")
    lines += ["", f"Total: {len(items)}."]

    msg = EmailMessage()
    msg["Subject"] = f"MBM: {len(items)} record(s) to archive - {datetime.now().date()}"
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(TO)
    msg.set_content("\n".join(lines))

    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

    save_sent(sent | {it["pid"] for it in items})
    print(f"archive digest: emailed {len(items)} record(s) to {', '.join(TO)}.")


if __name__ == "__main__":
    main()
