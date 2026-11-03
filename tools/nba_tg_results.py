#!/usr/bin/env python3
"""
nba_tg_results.py v1 - Telegram results alert for the 17:07 resolve (same message builder as
nba_telegram.py, which stays pinned in the slate task). Deterministic: the message is built
from run.json; the model never writes or edits the text.
Env: TG_TOKEN (bot token), TG_CHAT (chat id) - kept only in the task prompt, never in the repo.

Commands:
  send run.json [data.json]   one message for this run's picks (data.json adds "N picks today")
  preview run.json [data.json]  print the message, send nothing
  results sheet.csv plan.json|- [report.txt] [next.txt]
                              resolve summary: each pick settled by plan.json (W/L/PUSH/VOID, actual),
                              the rolling record (report.txt = output of nba_sheet_io.py report) and the
                              next-day line (next.txt); with plan "-" only report/next are sent
  test                        "alerts are working"

Exit: 0 sent, 3 nothing to send (run made no picks), 1 error.
"""
import sys, json, os, html, csv, io, re, urllib.request, urllib.parse

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


COLS = ("pick_id date tip_utc game player team stat std alt odds hit_pct hit_sample edge stars stake_u "
        "flags close_odds close_status clv_pct actual result resolved_at source_run engine_version "
        "safe_alt safe_odds safe_hit_sample").split()


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def fnum(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def results_text(sheet_path, plan_path, report_path, next_path):
    out = []
    if plan_path and plan_path != "-":
        rows = list(csv.DictReader(io.StringIO(open(sheet_path, encoding="utf-8").read().lstrip("\ufeff"))))
        plan = load(plan_path) or {}
        settled = []
        for b in plan.get("blocks", []):
            m = re.match(r"cohort!([A-Z]+)(\d+):([A-Z]+)(\d+)", b.get("range", ""))
            if not m:
                continue
            c0 = col_index(m.group(1))
            for k, vals in enumerate(b.get("values", [])):
                i = int(m.group(2)) + k - 2
                if not (0 <= i < len(rows)):
                    continue
                r = dict(rows[i])
                for j, v in enumerate(vals):
                    if c0 + j < len(COLS):
                        r[COLS[c0 + j]] = v
                if r.get("result"):
                    settled.append(r)
        if settled:
            w = sum(r["result"] == "W" for r in settled); l = sum(r["result"] == "L" for r in settled)
            u = sum((float(r["odds"]) - 1) if r["result"] == "W" else -1 if r["result"] == "L" else 0 for r in settled)
            out.append(f"📊 <b>Results: {w}-{l}</b>, {u:+.2f}u" + (f" · {len(settled) - w - l} void/push" if len(settled) - w - l else ""))
            icon = {"W": "✅", "L": "❌", "PUSH": "➖", "VOID": "⚪"}
            for g in sorted({r["game"] for r in settled}):
                out.append(f"\n<b>{esc(g)}</b>")
                for r in [x for x in settled if x["game"] == g]:
                    act = r.get("actual")
                    out.append(f"{icon.get(r['result'], '•')} {esc(r['player'])} {esc(r['stat'])} O{esc(fnum(r['alt']))} @{esc(fnum(r['odds']))}"
                               f" — {esc(fnum(act)) if act not in ('', None) else ('DNP' if r['result'] == 'VOID' else '—')}")
    if report_path and os.path.exists(report_path):
        rep_lines = [x.strip() for x in open(report_path, encoding="utf-8").read().splitlines() if x.strip()]
        keep = [x for x in rep_lines if x.startswith(("7d", "30d", "Season", "CLV"))]
        if keep:
            out.append("\n<i>" + "\n".join(esc(x) for x in keep) + "</i>")
    if next_path and os.path.exists(next_path):
        nl = open(next_path, encoding="utf-8").read().strip()
        if nl:
            out.append("\n🗓 " + esc(nl))
    if not out:
        return None
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
    if a[0] in ("results", "results-preview") and len(a) >= 3:
        text = results_text(a[1], a[2], a[3] if len(a) > 3 else None, a[4] if len(a) > 4 else None)
        if not text:
            print(json.dumps({"sent": False, "note": "nothing to report"}))
            sys.exit(3)
        if a[0] == "results-preview":
            print(text); return
        send(text); return
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
