#!/usr/bin/env python3
"""
nba_totals_io.py v1.1 - the only way scheduled tasks write the `totals` tab of the NBA Sheet.
Deterministic: the model never computes or formats a value; it copies plan blocks into
Google Sheets update_values calls and passes the tstats readback to `verify`.
Separate from nba_sheet_io.py so the prop pipeline (cohort / stats) is never touched.

Commands (totals.csv = fresh curl of the published `totals` tab CSV):
  record   data.json totals.csv        -> plan.json  (append today's analysed games; new keys only)
  history  hist.csv totals.csv         -> plan.json  (one-off: Notion Projected Totals rows, unpriced)
  pending  totals.csv [data.json]      -> games whose final total is due (exit 3 = none); with data.json,
                                          today's not-yet-recorded games count too once due (v1.1)
  actuals  totals.csv scores.json      -> plan.json  (fill final totals from The Odds API /scores)
  daily    data.json totals.csv scores.json -> plan.json (actuals + record in ONE plan; what the 18:07 resolve runs).
           v1.1: a newly recorded game that is already final in scores.json is settled in the same plan.
  verify   plan.json <rows> <unique> <finals>   (tstats!B2:B4) -> exit 0 match, 1 mismatch

Row = one game. Columns A-M:
  game_key date tip game proj mkt mkt_o mkt_u actual status source recorded_at settled_at
status: "" pending, "final" (actual = final score sum incl. OT), "void" (never written automatically).
The notional bet (lean, price, W/L, P/L) is NOT stored: readers derive it from these columns
using the Analyst's rules (claude/NBA_Totals_Tracker_Spec.md section B).

plan.json = {"blocks":[{"range","values"}],"expect":[rows,unique,finals],"summary","notes"}
Exit codes: 0 = blocks to write / ok, 3 = nothing to do, 1 = error.
"""
import sys, json, csv, io, re, os, datetime

COLS = "game_key date tip game proj mkt mkt_o mkt_u actual status source recorded_at settled_at".split()
LAST = "M"
NUMERIC = {"proj", "mkt", "mkt_o", "mkt_u", "actual"}
NAMES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
ALIAS = {"BRK": "BKN", "PHO": "PHX", "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
FINISH_AFTER = datetime.timedelta(hours=3, minutes=15)


def die(m):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(1)


def mel():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Australia/Melbourne")
    except Exception:
        die("zoneinfo unavailable")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def today_mel():
    return now_utc().astimezone(mel()).date()


def iso_now():
    return now_utc().isoformat(timespec="seconds")


def num(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s == "" or s.lower() in ("null", "none", "nan"):
        return ""
    try:
        f = float(s)
    except ValueError:
        return ""
    return int(f) if f.is_integer() else round(f, 3)


def code(t):
    t = str(t).strip().upper()
    return ALIAS.get(t, t)


def gid(g):
    g = str(g or "").strip()
    if "@" not in g:
        return None
    a, h = (code(x) for x in g.split("@", 1))
    return f"{a}@{h}"


def slate_iso(s):
    s = re.sub(r"^[A-Za-z]{3,}\s+", "", str(s or "").strip())
    for f in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def load_sheet(path):
    txt = open(path, encoding="utf-8").read().lstrip("﻿")
    if not txt.strip():
        return []
    rows = list(csv.DictReader(io.StringIO(txt)))
    if rows and set(COLS) - set(rows[0].keys()):
        die("totals header missing columns: " + ",".join(sorted(set(COLS) - set(rows[0].keys()))))
    if not rows:
        hdr = next(csv.reader(io.StringIO(txt)), [])
        if set(COLS) - set(h.strip() for h in hdr):
            die("totals header missing or wrong; expected " + ",".join(COLS))
    return rows


def cell(v, c):
    if v == "" or v is None:
        return ""
    return v if c in NUMERIC else str(v)


def expect(sheet_rows, appended, finals_added=0):
    keys = [r["game_key"] for r in sheet_rows] + [a[0] for a in appended]
    finals = sum(1 for r in sheet_rows if r.get("status") == "final") + finals_added
    return [len(keys), len(set(keys)), finals]


def append_plan(sheet, new_rows, label):
    have = {r["game_key"] for r in sheet}
    rows, notes, seen = [], [], set()
    for r in new_rows:
        k = r["game_key"]
        if k in have or k in seen:
            continue
        seen.add(k)
        rows.append([cell(r.get(c, ""), c) for c in COLS])
    blocks = []
    if rows:
        first = len(sheet) + 2
        blocks.append({"range": f"totals!A{first}:{LAST}{first + len(rows) - 1}", "values": rows})
    plan = {"blocks": blocks, "expect": expect(sheet, rows),
            "summary": f"{label}: {len(rows)} new game row(s)" + (f", {len(new_rows) - len(rows)} already recorded" if len(new_rows) - len(rows) else ""),
            "notes": notes}
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    sys.exit(0 if rows else 3)


def game_rows(data_path):
    try:
        d = json.load(open(data_path, encoding="utf-8"))
    except Exception as e:
        die(f"data.json unreadable: {e}")
    date = slate_iso(d.get("date"))
    if not date:
        die("data.json date unparseable: " + str(d.get("date")))
    out, ts = [], iso_now()
    for g in d.get("games", []) or []:
        i = gid(g.get("id"))
        if not i:
            continue
        out.append({"game_key": f"{date}|{i}", "date": date, "tip": str(g.get("tip") or ""), "game": i,
                    "proj": num(g.get("proj")), "mkt": num(g.get("mkt")), "mkt_o": num(g.get("mkt_o")),
                    "mkt_u": num(g.get("mkt_u")), "actual": "", "status": "", "source": "slate",
                    "recorded_at": ts, "settled_at": ""})
    return date, out


def new_rows(sheet, rows):
    have, seen, vals = {r["game_key"] for r in sheet}, set(), []
    for r in rows:
        if r["game_key"] in have or r["game_key"] in seen:
            continue
        seen.add(r["game_key"])
        vals.append([cell(r.get(c, ""), c) for c in COLS])
    return vals


def cmd_record(a):
    if len(a) < 2:
        die("usage: record data.json totals.csv")
    date, out = game_rows(a[0])
    append_plan(load_sheet(a[1]), out, f"totals record {date}")


def cmd_daily(a):
    if len(a) < 3:
        die("usage: daily data.json totals.csv scores.json")
    sheet = load_sheet(a[1])
    finals = parse_finals(a[2])
    blocks, notes, added = actual_blocks(sheet, a[2], finals)
    try:
        date, out = game_rows(a[0])
        vals = new_rows(sheet, out)
    except SystemExit:
        date, vals = "?", []
        notes.append("data.json unreadable - no games recorded this run")
    now, ts, same = now_utc(), iso_now(), 0
    for v in vals:                      # v1.1: settle a new row at once if it is due and final in the feed
        r = dict(zip(COLS, v))
        if finished(r, now) and r["game_key"] in finals:
            v[COLS.index("actual")], v[COLS.index("status")], v[COLS.index("settled_at")] = finals[r["game_key"]], "final", ts
            same += 1
    added += same
    if vals:
        first = len(sheet) + 2
        blocks.append({"range": f"totals!A{first}:{LAST}{first + len(vals) - 1}", "values": vals})
    plan = {"blocks": blocks, "expect": expect(sheet, vals, added),
            "summary": f"totals: {added} game(s) settled ({same} same night), {len(vals)} new game(s) recorded ({date})",
            "notes": notes}
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    sys.exit(0 if blocks else 3)


def cmd_history(a):
    if len(a) < 2:
        die("usage: history hist.csv totals.csv")
    sheet = load_sheet(a[1])
    out, ts = [], iso_now()
    for r in csv.DictReader(open(a[0], encoding="utf-8")):
        i, date = gid(r.get("game")), (r.get("date") or "").strip()
        if not i or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        act = num(r.get("actual"))
        out.append({"game_key": f"{date}|{i}", "date": date, "tip": "", "game": i,
                    "proj": num(r.get("proj")), "mkt": num(r.get("mkt")), "mkt_o": "", "mkt_u": "",
                    "actual": act, "status": "final" if act != "" else "", "source": "notion-history",
                    "recorded_at": ts, "settled_at": ts if act != "" else ""})
    append_plan(sheet, out, "totals history")


def finished(r, now):
    date = r.get("date", "")
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        return False
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (r.get("tip") or "").strip())
    if not m:  # no tip: due once the Melbourne day is over or late afternoon
        loc = now.astimezone(mel())
        return d < loc.date() or (d == loc.date() and loc.hour >= 17)
    tip = datetime.datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=mel())
    return now >= tip + FINISH_AFTER


def due_rows(sheet):
    now = now_utc()
    return [(i + 2, r) for i, r in enumerate(sheet) if not (r.get("status") or "").strip() and finished(r, now)]


def cmd_pending(a):
    if not a:
        die("usage: pending totals.csv [data.json]")
    sheet = load_sheet(a[0])
    due = due_rows(sheet)
    out = [{"row": n, "game_key": r["game_key"], "date": r["date"], "game": r["game"]} for n, r in due]
    if len(a) > 1:                      # v1.1: today's games not yet in the tab, once due
        try:
            _, rows = game_rows(a[1])
            have, now = {r["game_key"] for r in sheet}, now_utc()
            out += [{"row": None, "game_key": r["game_key"], "date": r["date"], "game": r["game"]}
                    for r in rows if r["game_key"] not in have and finished(r, now)]
        except SystemExit:
            pass
    print(json.dumps(out, indent=1))
    sys.exit(0 if out else 3)


def parse_finals(scores_path):
    try:
        sc = json.load(open(scores_path, encoding="utf-8"))
    except Exception as e:
        die(f"scores.json unreadable: {e}")
    if not isinstance(sc, list):
        die("scores.json is not a list (API error?)")
    finals = {}
    for e in sc:
        if not isinstance(e, dict) or not e.get("completed") or not e.get("scores"):
            continue
        h, aw = NAMES.get(e.get("home_team")), NAMES.get(e.get("away_team"))
        if not h or not aw:
            continue
        pts = {}
        for s in e["scores"]:
            try:
                pts[s["name"]] = float(s["score"])
            except (KeyError, TypeError, ValueError):
                pts = None
                break
        if not pts or len(pts) != 2:
            continue
        try:
            t = datetime.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")).astimezone(mel())
        except Exception:
            continue
        tot = num(sum(pts.values()))
        finals[f"{t.date().isoformat()}|{aw}@{h}"] = tot
    return finals


def actual_blocks(sheet, scores_path, finals=None):
    finals = parse_finals(scores_path) if finals is None else finals
    blocks, notes, ts, added = [], [], iso_now(), 0
    for n, r in due_rows(sheet):
        k = r["game_key"]
        if k not in finals:
            notes.append(f"{k}: no completed score in feed yet")
            continue
        vals = [finals[k], "final", r.get("source", ""), r.get("recorded_at", ""), ts]
        blocks.append({"range": f"totals!I{n}:{LAST}{n}", "values": [[cell(v, c) for v, c in zip(vals, COLS[8:])]]})
        added += 1
    return blocks, notes, added


def cmd_actuals(a):
    if len(a) < 2:
        die("usage: actuals totals.csv scores.json")
    sheet = load_sheet(a[0])
    blocks, notes, added = actual_blocks(sheet, a[1])
    plan = {"blocks": blocks, "expect": expect(sheet, [], added),
            "summary": f"totals actuals: {added} game(s) settled" + (f", {len(notes)} still waiting" if notes else ""),
            "notes": notes}
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    sys.exit(0 if blocks else 3)


def cmd_verify(a):
    if len(a) < 4:
        die("usage: verify plan.json <rows> <unique> <finals>")
    exp = json.load(open(a[0], encoding="utf-8")).get("expect")
    got = [num(x) for x in a[1:4]]
    bad = [f"{n}: expected {e}, sheet {g if g != '' else 'blank'}" for n, e, g in zip(("rows", "unique", "finals"), exp, got) if num(e) != g]
    print(json.dumps({"ok": not bad, "mismatches": bad}))
    sys.exit(1 if bad else 0)


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    cmd = {"record": cmd_record, "history": cmd_history, "pending": cmd_pending,
           "actuals": cmd_actuals, "daily": cmd_daily, "verify": cmd_verify}.get(a[0]) or die("unknown command " + a[0])
    cmd(a[1:])


if __name__ == "__main__":
    main()
