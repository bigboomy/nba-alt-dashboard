#!/usr/bin/env python3
"""
nba_hp.py v1 - HaterPicks odds for the NBA tasks (Mitch approved 11 Nov 2026; spec claude/NBA_HaterPicks_Migration_Spec.md).
One /api/v1/props call returns the whole NBA board (free plan: a copy refreshed every 30 min, 1,000 calls a month).
Env: HP_KEY (sent as "Authorization: Bearer"; kept only in the task prompt - never in a file, commit or output line).

Commands:
  fetch props|odds OUT.json     one HaterPicks call; saves the body (+ "_hp" meta) to OUT.json; prints meta.
                                exit 0 ok, 1 failed (HTTP/JSON/error body), 4 quota under 50 (body saved, do not use)
  alts props.json [GAMES]       -> stdout: alts.json in the odds_fetch.py "alts" shape (Pointsbet as "pointsbetau",
                                plus "sportsbet" ladders for v3.19, ignored until then), espn ids kept. GAMES = AWY@HOM,...
  totals odds.json [GAMES]      -> stdout: {"AWY@HOM": {"mkt", "mkt_o", "mkt_u"}} from Pointsbet game totals (balanced line)
  capture sheet.csv props.json  -> stdout: plan.json (close_odds / close_status / clv_pct + close_copied_at), verified with
                                nba_sheet_io.py verify (needs nba_sheet_io.py in the same folder)
  espn-scores [DAYS]            -> stdout: ESPN finals for the last DAYS US dates (default 3) in the Odds API scores shape,
                                for nba_totals_io.py daily. Free, no key.
Stale rungs (HaterPicks "current": false) are dropped everywhere.
"""
import sys, json, os, re, difflib, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

BASE = "https://www.haterpicks.app/api/v1"
UA = {"User-Agent": "nba-alt-dashboard/1 (+https://bigboomy.github.io/nba-alt-dashboard/)"}
QUOTA_FLOOR = 50
STAT = {"NBA_POINTS": "pts", "NBA_REBOUNDS": "reb", "NBA_ASSISTS": "ast"}
STAT_SHEET = {"PTS": "NBA_POINTS", "REB": "NBA_REBOUNDS", "AST": "NBA_ASSISTS"}
CODE = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
FULL = {v: k for k, v in CODE.items() if k != "LA Clippers"}
ESPN_ABBR = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "BRK": "BKN"}


def die(m, code=1):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(code)


def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception as e:
        die(f"{p} unreadable: {e}")


def gid(away, home):
    a, h = CODE.get(away), CODE.get(home)
    return f"{a}@{h}" if a and h else None


def games_arg(a, i):
    return {g.strip().upper() for g in a[i].split(",") if g.strip()} if len(a) > i and a[i].strip() else None


# ---------------- fetch ----------------
def cmd_fetch(a):
    if len(a) < 2 or a[0] not in ("props", "odds"):
        die("usage: fetch props|odds OUT.json")
    key = os.environ.get("HP_KEY", "").strip() or die("HP_KEY not set")
    req = urllib.request.Request(f"{BASE}/{a[0]}?sport=nba", headers=dict(UA, Authorization="Bearer " + key))
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body, hdr, code = r.read(), {k.lower(): v for k, v in r.headers.items()}, r.status
    except urllib.error.HTTPError as e:
        die(f"HaterPicks {a[0]}: HTTP {e.code}")
    except Exception as e:
        die(f"HaterPicks {a[0]} unreachable: {type(e).__name__}")
    try:
        d = json.loads(body)
    except Exception:
        die(f"HaterPicks {a[0]}: response is not JSON")
    if not isinstance(d, dict) or d.get("error") or not isinstance(d.get("data"), (dict, list)):
        die(f"HaterPicks {a[0]}: error body: {str(d.get('error') if isinstance(d, dict) else d)[:200]}")
    try:
        rem = int(hdr.get("x-quota-remaining"))
    except (TypeError, ValueError):
        rem = None
    meta = {"feed": a[0], "quota_remaining": rem, "quota_limit": hdr.get("x-quota-limit"),
            "copied_at": d.get("copied_at") or (d.get("data") or {}).get("generated_at") if isinstance(d.get("data"), dict) else d.get("generated_at"),
            "sample": d.get("sample"), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if a[0] == "props":
        mk = (d.get("data") or {}).get("markets") or []
        meta["rows"] = len(mk)
        meta["pointsbet_last_seen"] = ((d.get("data") or {}).get("books_last_seen") or {}).get("pointsbet")
        if not mk:
            die("HaterPicks props: empty board")
    d["_hp"] = meta
    json.dump(d, open(a[1], "w", encoding="utf-8"))
    print(json.dumps(meta))
    if rem is not None and rem < QUOTA_FLOOR:
        print(json.dumps({"error": f"HaterPicks quota low: {rem} calls left this month (floor {QUOTA_FLOOR})"}), file=sys.stderr)
        sys.exit(4)


# ---------------- props -> alts.json ----------------
def rung_list(p, notes):
    out = []
    if not isinstance(p, dict):
        return out
    if p.get("current") is False:
        notes["stale"] = notes.get("stale", 0) + 1
        return out
    return p


def infer_standard(stat):
    stat["std_inferred"] = {}
    for book, ladder in stat["alt"].items():
        overs = [r for r in ladder if r["side"] == "Over"]
        if overs:
            best = dict(min(overs, key=lambda r: abs(r["price"] - 1.89)))
            best["confident"] = abs(best["price"] - 1.89) <= 0.15
            stat["std_inferred"][book] = best


def cmd_alts(a):
    d = load(a[0]) if a else die("usage: alts props.json [GAMES]")
    keep = games_arg(a, 1)
    mk = (d.get("data") or {}).get("markets") or die("props.json has no markets")
    evs, notes = {}, {}
    for x in mk:
        stat = STAT.get(x.get("market"))
        if not stat:
            continue
        g = gid(x.get("away_team"), x.get("home_team"))
        if not g or (keep is not None and g not in keep):
            continue
        ev = evs.setdefault(g, {"event_id": "hp:" + str(x.get("game_key")), "away": FULL[g.split("@")[0]],
                                "home": FULL[g.split("@")[1]], "tip": x.get("game_start_iso"),
                                "espn_game_id": x.get("espn_game_id"), "game": g, "props": {}})
        pl = ev["props"].setdefault(x["player_name"], {})
        b = pl.setdefault(stat, {"std": {"pointsbetau": {}, "sportsbet": {}},
                                 "alt": {"pointsbetau": [], "sportsbet": []},
                                 "espn_player_id": x.get("espn_player_id"), "team": x.get("team")})
        for hp_book, key in (("pointsbet", "pointsbetau"), ("sportsbet", "sportsbet")):
            p = rung_list((x.get("prices") or {}).get(hp_book), notes)
            for side in ("over", "under"):
                if p and isinstance(p.get(side), (int, float)) and x.get("line") is not None:
                    b["alt"][key].append({"line": float(x["line"]), "price": float(p[side]), "side": side.title()})
    out = []
    for g in sorted(evs, key=lambda k: (evs[k]["tip"] or "", k)):
        ev = evs[g]
        for pl in list(ev["props"]):
            for st in list(ev["props"][pl]):
                b = ev["props"][pl][st]
                for book in b["alt"]:
                    b["alt"][book].sort(key=lambda r: (r["side"], r["line"]))
                if not b["alt"]["pointsbetau"] and not b["alt"]["sportsbet"]:
                    del ev["props"][pl][st]
                else:
                    infer_standard(b)
            if not ev["props"][pl]:
                del ev["props"][pl]
        out.append(ev)
    missing = sorted(keep - set(evs)) if keep else []
    meta = d.get("_hp") or {}
    print(json.dumps({"events": out,
                      "_errors": [{g: "not on the HaterPicks board"} for g in missing],
                      "_quota": {"remaining": meta.get("quota_remaining"), "used_this_call": 1, "source": "haterpicks",
                                 "copied_at": meta.get("copied_at") or d.get("copied_at"),
                                 "pointsbet_last_seen": meta.get("pointsbet_last_seen"),
                                 "stale_rungs_dropped": notes.get("stale", 0)}}, ensure_ascii=False, indent=1))


# ---------------- odds -> market totals ----------------
def cmd_totals(a):
    d = load(a[0]) if a else die("usage: totals odds.json [GAMES]")
    keep = games_arg(a, 1)
    rows = d.get("data") if isinstance(d.get("data"), list) else (d.get("data") or {}).get("markets") or []
    by = {}
    for x in rows:
        if x.get("market") != "NBA_TOTAL":
            continue
        g = gid(x.get("away_team"), (x.get("matchup") or "").split(" @ ")[-1])
        if not g or (keep is not None and g not in keep):
            continue
        p = (x.get("prices") or {}).get("pointsbet") or {}
        if p.get("current") is False or not isinstance(p.get("over"), (int, float)) or not isinstance(p.get("under"), (int, float)):
            continue
        by.setdefault(g, []).append((abs(p["over"] - p["under"]), 0 if x.get("main") else 1, float(x["line"]), p["over"], p["under"]))
    out = {}
    for g in (sorted(keep) if keep else sorted(by)):
        c = sorted(by.get(g, []))
        out[g] = {"mkt": c[0][2], "mkt_o": c[0][3], "mkt_u": c[0][4]} if c else {"mkt": None, "mkt_o": None, "mkt_u": None}
    print(json.dumps(out))


# ---------------- capture (closing price from the props copy) ----------------
def cmd_capture(a):
    if len(a) < 2:
        die("usage: capture sheet.csv props.json")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nba_sheet_io as sio
    rows = sio.load_sheet(a[0])
    d = load(a[1])
    mk = (d.get("data") or {}).get("markets") or die("props.json has no markets")
    copied = (d.get("_hp") or {}).get("copied_at") or d.get("copied_at") or ""
    pend = [(i + 2, r) for i, r in enumerate(rows) if r["odds"] != "" and r["close_status"] == "" and r["result"] == ""]
    if not pend:
        print(json.dumps({"message": "No picks awaiting a close"})); sys.exit(3)
    now = datetime.now(timezone.utc)
    board = {}
    for x in mk:
        g = gid(x.get("away_team"), x.get("home_team"))
        if g:
            board.setdefault(g, {"tip": x.get("game_start_iso"), "rows": []})["rows"].append(x)
    upd, results = {}, []
    for n, r in pend:
        g = str(r["game"]).strip().upper()
        tip = r["tip_utc"] or (board.get(g) or {}).get("tip")
        status = close = clv = ""
        try:
            t = datetime.fromisoformat(str(tip).replace("Z", "+00:00"))
        except Exception:
            t = None
        if t is None:
            if r["date"] <= sio.today_mel().isoformat() and g not in board:
                status = "tipped_pre_capture"
            else:
                continue
        else:
            lead = t - now
            if lead > timedelta(minutes=sio.MAX_LEAD):
                continue
            if lead < timedelta(minutes=sio.MIN_LEAD):
                if lead < timedelta(0):
                    status = "tipped_pre_capture"
                else:
                    continue
        if not status:
            cand = [x for x in (board.get(g) or {}).get("rows", []) if x.get("market") == STAT_SHEET.get(r["stat"])]
            if g not in board:
                status = "pulled"
            else:
                names = {x["player_name"] for x in cand}
                nm = r["player"] if r["player"] in names else next((x for x in names if sio.norm(x) == sio.norm(r["player"])), None)
                if nm is None:
                    near = difflib.get_close_matches(sio.norm(r["player"]), [sio.norm(x) for x in names], n=1, cutoff=0.85)
                    status = "name_mismatch" if near else "pulled"
                else:
                    pr = None
                    for x in cand:
                        if x["player_name"] == nm and x.get("line") is not None and abs(float(x["line"]) - float(r["alt"])) < 1e-9:
                            p = (x.get("prices") or {}).get("pointsbet") or {}
                            if p.get("current") is not False and isinstance(p.get("over"), (int, float)):
                                pr = float(p["over"])
                    if pr:
                        close, status = sio.num(pr), "ok"
                        clv = sio.num(round((float(r["odds"]) / pr - 1) * 100, 1))
                    else:
                        status = "line_gone"
        r = dict(r)
        r["close_odds"], r["close_status"], r["clv_pct"] = close, status, clv
        r["_copied"] = copied if status == "ok" else ""
        upd[n] = r
        results.append(f'{r["player"]} {r["stat"]} {sio.fmt(r["alt"])}: {sio.fmt(close) or status}' + (f" (CLV {clv:+.1f}%)" if clv != "" else ""))
    if not upd:
        print(json.dumps({"message": "No picks due for a close in this window", "pending": len(pend)})); sys.exit(3)
    after = list(rows)
    for n, r in upd.items():
        after[n - 2] = {k: v for k, v in r.items() if k != "_copied"}
    blocks = sio.blocks_for({n: {k: v for k, v in r.items() if k != "_copied"} for n, r in upd.items()}, "close_odds", "clv_pct")
    for n in sorted(upd):
        blocks.append({"range": f"cohort!AB{n}:AB{n}", "values": [[upd[n]["_copied"]]]})
    oks = [r["clv_pct"] for r in upd.values() if r["clv_pct"] != ""]
    mean = f"{sum(oks)/len(oks):+.1f}%" if oks else "n/a"
    flagged = [x for x in results if "CLV" not in x]
    rem = (d.get("_hp") or {}).get("quota_remaining")
    summary = (f"Closes captured for {len(oks)}/{len(upd)} picks due (HaterPicks copy {copied}). Mean CLV {mean}. "
               + (("Flagged: " + "; ".join(flagged) + ". ") if flagged else "")
               + f"HaterPicks: {rem if rem is not None else '?'}/1000 calls left.")
    print(json.dumps({"blocks": blocks, "expect": sio.stats(after), "summary": summary, "notes": results}, ensure_ascii=False, indent=1))
    sys.exit(0)


# ---------------- ESPN finals -> Odds API scores shape ----------------
def cmd_espn_scores(a):
    days = int(a[0]) if a and a[0].isdigit() else 3
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    out, errs = [], 0
    for k in range(days + 1):
        dt = (today - timedelta(days=k)).strftime("%Y%m%d")
        try:
            req = urllib.request.Request(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={dt}", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                sb = json.loads(r.read())
        except Exception:
            errs += 1; continue
        for e in sb.get("events", []):
            c = (e.get("competitions") or [{}])[0]
            teams = {}
            for t in c.get("competitors", []):
                ab = t["team"].get("abbreviation", "")
                code = ESPN_ABBR.get(ab, ab)
                if code not in FULL:
                    code = CODE.get(t["team"].get("displayName"))
                if code:
                    teams[t.get("homeAway")] = (FULL[code], t.get("score"))
            if "home" not in teams or "away" not in teams:
                continue
            done = bool(((e.get("status") or {}).get("type") or {}).get("completed"))
            out.append({"id": "espn:" + str(e.get("id")), "home_team": teams["home"][0], "away_team": teams["away"][0],
                        "commence_time": e.get("date"), "completed": done,
                        "scores": [{"name": teams["home"][0], "score": str(teams["home"][1])},
                                   {"name": teams["away"][0], "score": str(teams["away"][1])}] if done else None})
    if errs > days:
        die("ESPN scoreboard unreachable")
    print(json.dumps(out))


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    cmds = {"fetch": cmd_fetch, "alts": cmd_alts, "totals": cmd_totals, "capture": cmd_capture, "espn-scores": cmd_espn_scores}
    (cmds.get(a[0]) or (lambda _: die("unknown command " + a[0])))(a[1:])


if __name__ == "__main__":
    main()
