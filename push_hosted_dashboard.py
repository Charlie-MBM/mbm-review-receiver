#!/usr/bin/env python3
"""
Push the current dashboard_index.html to the private hosted copy (Cloudflare Worker
`mbm-dashboard`), so Charlie's business partner sees the latest daily bake at a
stable, unguessable URL — no Cowork session required on their end.

WHY a worker (not KV): the CLOUDFLARE_API_TOKEN in .env has Workers Scripts:Edit
but NOT Workers KV:Edit, so the HTML is inlined (base64) into the worker script and
re-uploaded each run. Aggregate counts only — no PHI — so hosting is fine.

Run as the LAST step of the daily dashboard bake (after dashboard_index.html is
verified + the artifact is published). Idempotent; safe to re-run.

Hosted URL:  https://mbm-dashboard.charlie-956.workers.dev/dash-8a19bca7b8cbc5f3724077d0
"""
import base64, json, os, sys, urllib.request

HERE       = os.path.dirname(os.path.abspath(__file__))
HTML_PATH  = os.path.join(HERE, "dashboard_index.html")
ENV_PATH   = os.path.join(HERE, ".env")
ACCOUNT_ID = "95619789467f5b8aa49e44428e1ed443"   # current CF account (post 2026-07-17 move)
WORKER     = "mbm-dashboard"
SECRET_PATH = "/dash-8a19bca7b8cbc5f3724077d0"     # shared-with-partner secret path (keep stable)
API = "https://api.cloudflare.com/client/v4"

# Read-only tweak for the hosted (non-Cowork) view: hide the live-refresh button,
# which can't work without the Cowork MCP bridge.
INJECT = ("<script>(function(){if(!(window.cowork&&window.cowork.callMcpTool)){"
          "document.addEventListener('DOMContentLoaded',function(){"
          "var b=document.getElementById('refreshBtn');if(b)b.style.display='none';"
          "});}})();</script>")


def token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if t:
        return t.strip()
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("CLOUDFLARE_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("CLOUDFLARE_API_TOKEN not found in env or .env")


def build_worker():
    html = open(HTML_PATH, encoding="utf-8").read()
    html = html.replace("</body>", INJECT + "</body>", 1) if "</body>" in html else html + INJECT
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return (
        'const B64="' + b64 + '";\n'
        'const PATH="' + SECRET_PATH + '";\n'
        "export default{async fetch(request){\n"
        " const url=new URL(request.url);\n"
        " const p=url.pathname.replace(/\\/$/,'');\n"
        " if(p===PATH){\n"
        "  const bin=atob(B64);const bytes=new Uint8Array(bin.length);\n"
        "  for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);\n"
        "  return new Response(bytes,{headers:{'content-type':'text/html; charset=utf-8',"
        "'cache-control':'no-store','x-robots-tag':'noindex, nofollow, noarchive'}});\n"
        " }\n"
        " return new Response('Not found',{status:404,headers:{'x-robots-tag':'noindex'}});\n"
        "}};\n"
    )


def upload(worker_js, tok):
    boundary = "----mbmdash7f3c"
    meta = json.dumps({"main_module": "worker.js", "compatibility_date": "2026-07-01"})
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
                 f"Content-Type: application/json\r\n\r\n{meta}\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\n"
                 f"Content-Type: application/javascript+module\r\n\r\n{worker_js}\r\n")
    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def enable_subdomain(tok):
    req = urllib.request.Request(
        f"{API}/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER}/subdomain",
        data=json.dumps({"enabled": True, "previews_enabled": False}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    tok = token()
    res = upload(build_worker(), tok)
    if not res.get("success"):
        print("UPLOAD FAILED:", res.get("errors"), file=sys.stderr)
        sys.exit(1)
    enable_subdomain(tok)
    print("Pushed hosted dashboard OK -> "
          "https://mbm-dashboard.charlie-956.workers.dev" + SECRET_PATH)


if __name__ == "__main__":
    main()
