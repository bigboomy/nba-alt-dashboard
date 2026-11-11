#!/usr/bin/env python3
"""
nba_ladder_io.py v1.1 - alt-ladder log: every Pointsbet alt rung the slate saw, one row per player/stat ladder.
Source: the slate's own alts.json - `odds_fetch.py alts` (The Odds API) or `nba_hp.py alts` (HaterPicks, from 12 Nov).
v1.1: for HaterPicks data, captured_utc = the copy's copied_at (when the prices were taken) and book = "pointsbet".
Writes only the `ladders` tab (+ reads `kstats` for verify). Never touches cohort / stats / totals / leans.
Deterministic: the model copies plan blocks into update_values calls.

Commands:
  plan    alts.json <rows> [games]         -> kplan.json  (append this run's ladders after <rows> existing data rows,
                                              = kstats!B2 read just before; games = comma list of AWY@HOM to keep,
                                              default all events in alts.json). Needs no published CSV.
  verify  kplan.json <2 values from kstats!B2:B3>   -> exit 0 match, 1 mismatch

Row (columns A-N):
  ladder_id  date  game  tip_utc  captured_utc  mins_to_tip  player  stat  std_line  std_price
  over_rungs  under_rungs  n_over  book
ladder_id = <date>|<AWY@HOM>|<player>|<stat>|<captured_utc>   (a re-run of a game adds a new capture, never overwrites)
over_rungs / under_rungs = "line:price" pairs, ascending by line, space-separated, e.g. "10.5:1.22 12.5:1.40 14.5:1.70"
Exit codes: 0 = blocks to write / ok, 3 = nothing to do, 1 = error.
"""
import sys, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
TAB = "ladders"
BOOK = "pointsbetau"
COLS = ("ladder_id date game tip_utc captured_utc mins_to_tip player stat std_line std_price "
        "over_rungs under_rungs n_over book").split()
LAST = "N"
NUMERIC = {"mins_to_tip", "std_line", "std_price", "n_over"}
CODES = {
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
STAT = {"pts": "PTS", "reb": "REB", "ast": "AST"}


def die(m):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(1)


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return int(f) if f.is_integer() else round(f, 4)


def fmt(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def rungs(lst, side):
    pts = sorted({(float(r["line"]), float(r["price"])) for r in lst
                  if r.get("side") == side and r.get("line") is not None and r.get("price") is not None})
    return " ".join(f"{fmt(num(l))}:{fmt(num(p))}" for l, p in pts), len(pts)


def cmd_plan(a):
    if len(a) < 2:
        die("usage: plan alts.json <rows from kstats!B2> [AWY@HOM,...]")
    try:
        src = json.load(open(a[0], encoding="utf-8"))
    except Exception as e:
        die(f"alts.json unreadable: {e}")
    evs = src.get("events") if isinstance(src, dict) else None
    if not isinstance(evs, list):
        die("alts.json has no events list (is it the odds_fetch.py alts output?)")
    keep = {g.strip().upper() for g in a[2].split(",") if g.strip()} if len(a) > 2 else None
    n0 = num(str(a[1]).strip() or 0)
    if n0 == "" or n0 < 0:
        die("rows must be the number in kstats!B2")
    have = set()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    q = src.get("_quota") if isinstance(src.get("_quota"), dict) else {}
    hp = q.get("source") == "haterpicks"
    if hp and q.get("copied_at"):
        try:
            now = datetime.fromisoformat(str(q["copied_at"]).replace("Z", "+00:00")).astimezone(timezone.utc).replace(second=0, microsecond=0)
        except ValueError:
            pass
    cap = now.isoformat().replace("+00:00", "Z")
    book_out = "pointsbet" if hp else BOOK
    new, notes, games = [], [], set()
    for ev in evs:
        try:
            aw, hm = CODES[ev["away"]], CODES[ev["home"]]
            tip = datetime.fromisoformat(str(ev["tip"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            notes.append(f"skipped event {ev.get('event_id')}: unknown team or tip"); continue
        gid = f"{aw}@{hm}"
        if keep is not None and gid not in keep:
            continue
        date = tip.astimezone(MEL).date().isoformat()
        mins = int(round((tip - now).total_seconds() / 60))
        n_game = 0
        for player, stats in sorted((ev.get("props") or {}).items()):
            for sk, b in sorted((stats or {}).items()):
                stat = STAT.get(sk)
                ladder = ((b or {}).get("alt") or {}).get(BOOK) or []
                if not stat or not ladder:
                    continue
                over, n_over = rungs(ladder, "Over")
                under, _ = rungs(ladder, "Under")
                if not over and not under:
                    continue
                std = ((b.get("std_inferred") or {}).get(BOOK)) or {}
                lid = f"{date}|{gid}|{player}|{stat}|{cap}"
                if lid in have:
                    continue
                have.add(lid)
                new.append({"ladder_id": lid, "date": date, "game": gid,
                            "tip_utc": tip.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "captured_utc": cap, "mins_to_tip": mins, "player": player, "stat": stat,
                            "std_line": num(std.get("line")), "std_price": num(std.get("price")),
                            "over_rungs": over, "under_rungs": under, "n_over": n_over, "book": book_out})
                n_game += 1
        games.add(gid)
        if not n_game:
            notes.append(f"{gid}: no Pointsbet alt ladders in alts.json")
    blocks = []
    if new:
        first = n0 + 2
        vals = [[(r[c] if c in NUMERIC else str(r[c])) if r[c] != "" else "" for c in COLS] for r in new]
        for i in range(0, len(vals), 60):
            chunk = vals[i:i + 60]
            blocks.append({"range": f"{TAB}!A{first + i}:{LAST}{first + i + len(chunk) - 1}", "values": chunk})
    plan = {"blocks": blocks, "expect": [n0 + len(new), n0 + len(new)],
            "summary": f"{len(new)} ladders logged for {len(games)} game(s) ({', '.join(sorted(games)) or '-'})",
            "notes": notes}
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    sys.exit(0 if blocks else 3)


def cmd_verify(a):
    if len(a) != 3:
        die("verify needs kplan.json and the 2 values from kstats!B2:B3")
    exp = json.load(open(a[0], encoding="utf-8"))["expect"]
    bad = [f"{n}: expected {e}, Sheet shows {g}" for n, e, g in zip(("rows", "unique"), exp, a[1:])
           if num(g) != e]
    print(json.dumps({"ok": not bad, "mismatches": bad}))
    sys.exit(0 if not bad else 1)


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    {"plan": cmd_plan, "verify": cmd_verify}.get(a[0], lambda _: die("unknown command " + a[0]))(a[1:])


if __name__ == "__main__":
    main()
