#!/usr/bin/env python3
"""
nba_telegram.py v1 - Telegram pick alert for one slate run. Deterministic: the message is built
from run.json; the model never writes or edits the text.
Env: TG_TOKEN (bot token), TG_CHAT (chat id) - kept only in the task prompt, never in the repo.

Commands:
  send run.json [data.json]   one message for this run's picks (data.json adds "N picks today")
  preview run.json [data.json]  print the message, send nothing
  test                        "alerts are working"

Exit: 0 sent, 3 nothing to send (run made no picks), 1 error.
"""
import sys, json, os, html, urllib.request, urllib.parse

URL = "https://bigboomy.github.io/nba-alt-dashboard/"
LIMIT = 3900


def die(m):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(1)


def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=False)


def picks(obj):
    return [(g, p) for g in (obj or {}).get("games", []) or [] for p in (g.get("picks") or [])]


def stake(p):
    for f in p.get("f") or []:
        if isinstance(f, str) and f.startswith("U"):
            return f
    return ""


def build(run, data):
    mine = picks(run)
    if not mine:
        return None
    n = len(mine)
    total = len(picks(data)) if data else n
    out = [f"🏀 <b>{n} new pick{'s' if n != 1 else ''}</b>" + (f" · {total} today" if total > n else "")]
    for g in run.get("games", []) or []:
        ps = g.get("picks") or []
        if not ps:
            continue
        hdr = f"\n<b>{esc(g.get('id'))}</b> {esc(g.get('tip'))}"
        if g.get("proj") is not None and g.get("mkt") is not None:
            hdr += f"  <i>proj {esc(g.get('proj'))} v {esc(g.get('mkt'))}</i>"
        out.append(hdr)
        for p in ps:
            st = p.get("st")
            line = f"• {esc(p.get('p'))} {esc(p.get('s'))} <b>O{esc(p.get('alt'))}</b> @{esc(p.get('o'))}"
            meta = " · ".join(x for x in [esc(p.get("h")), (f"{esc(st)}★" if st not in (None, "") else ""), esc(stake(p))] if x)
            if meta:
                line += f"  <i>{meta}</i>"
            out.append(line)
            if p.get("safe_alt") is not None and p.get("safe_o") is not None:
                out.append(f"   ↳ safer O{esc(p.get('safe_alt'))} @{esc(p.get('safe_o'))}" + (f" ({esc(p.get('safe_h'))})" if p.get("safe_h") else ""))
    out.append(f'\n<a href="{URL}">Open dashboard</a>')
    text = "\n".join(out)
    if len(text) > LIMIT:
        text = text[:LIMIT].rsplit("\n", 1)[0] + f'\n… more on the <a href="{URL}">dashboard</a>'
    return text


def send(text):
    tok, chat = os.environ.get("TG_TOKEN", "").strip(), os.environ.get("TG_CHAT", "").strip()
    if not tok or not chat:
        die("TG_TOKEN / TG_CHAT not set")
    body = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        die(f"telegram unreachable: {type(e).__name__} {e}")
    if not d.get("ok"):
        die("telegram refused: " + str(d.get("description")))
    print(json.dumps({"sent": True, "message_id": d.get("result", {}).get("message_id"), "chars": len(text)}))


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    if a[0] == "test":
        send("✅ <b>NBA alerts are working.</b>\nSlate picks will arrive here.")
        return
    if a[0] in ("send", "preview") and len(a) >= 2:
        run = load(a[1])
        if run is None:
            die("run.json unreadable")
        text = build(run, load(a[2]) if len(a) > 2 else None)
        if not text:
            print(json.dumps({"sent": False, "note": "run made no picks"}))
            sys.exit(3)
        if a[0] == "preview":
            print(text)
            return
        send(text)
        return
    die(__doc__)


if __name__ == "__main__":
    main()
