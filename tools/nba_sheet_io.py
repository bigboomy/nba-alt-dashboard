#!/usr/bin/env python3
"""
nba_sheet_io.py v1.1 - the only way scheduled tasks write the NBA cohort Sheet.
Deterministic: the model never computes or formats a value; it copies plan
blocks into Google Sheets update_values calls and passes the stats readback
to `verify`.

Commands (sheet.csv = fresh curl of the published cohort CSV):
  slate   data.json sheet.csv               -> plan.json  (append today's picks)
  capture sheet.csv                         -> plan.json  (closing odds; needs ODDS_API_KEY, curl)
  pending sheet.csv                         -> list of finished games/picks still unresolved
  resolve sheet.csv actuals.json            -> plan.json  (settle picks; actuals = {pick_id: number|"DNP"})
  verify  plan.json <8 values from stats!B2:B9>   -> exit 0 match, 1 mismatch
  report  sheet.csv [plan.json]             -> rolling 7d/30d/season block (plan applied in memory)
  done    current.json sheet.csv            -> {"date", "games_done": [...]} games already analysed today
  merge-data run.json [current.json]        -> data.json: this run's games merged into today's file

plan.json = {"blocks": [{"range": "cohort!A5:AA7", "values": [[...]]}], "expect": [8 values],
             "summary": "...", "notes": [...]}
Exit codes: 0 = blocks to write / ok, 3 = nothing to do, 1 = error.
"""
import sys, os, json, csv, io, re, subprocess, tempfile, unicodedata, difflib
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
SEASON_START = "2026-10-21"
COLS = ("pick_id date tip_utc game player team stat std alt odds hit_pct hit_sample edge stars stake_u "
        "flags close_odds close_status clv_pct actual result resolved_at source_run engine_version "
        "safe_alt safe_odds safe_hit_sample").split()
LETTER = {c: (chr(65 + i) if i < 26 else "A" + chr(65 + i - 26)) for i, c in enumerate(COLS)}
NUMERIC = {"std", "alt", "odds", "hit_pct", "edge", "stars", "stake_u", "close_odds", "clv_pct",
           "actual", "safe_alt", "safe_odds"}
BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
MIN_LEAD, MAX_LEAD = 2, 70   # capture a pick once, 2-70 min before its tip (hourly runs at :48)
MARKET = {"PTS": "player_points_alternate", "REB": "player_rebounds_alternate", "AST": "player_assists_alternate"}
TEAMS = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets", "BRK": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}


def die(m):
    print(json.dumps({"error": m}), file=sys.stderr); sys.exit(1)


def num(s):
    if s is None:
        return ""
    if isinstance(s, (int, float)):
        v = float(s)
    else:
        s = str(s).strip().replace("−", "-").lstrip("+").rstrip("%")
        try:
            v = float(s)
        except ValueError:
            return ""
    return int(v) if v.is_integer() else round(v, 4)


def fmt(v):
    if v == "" or v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def load_sheet(path):
    rows = list(csv.DictReader(io.StringIO(open(path, encoding="utf-8").read())))
    if rows and set(COLS) - set(rows[0].keys()):
        die("sheet header missing columns: " + ",".join(sorted(set(COLS) - set(rows[0].keys()))))
    if not rows:
        die("sheet.csv has no rows - fetch failed?")
    out = []
    for r in rows:
        d = {c: r.get(c, "") for c in COLS}
        for c in NUMERIC:
            d[c] = num(d[c]) if d[c] != "" else ""
        out.append(d)
    return out


def cellv(r, c):
    v = r[c]
    if v == "" or v is None:
        return ""
    return v if c in NUMERIC else str(v)


def blocks_for(rows_by_num, c1, c2):
    """rows_by_num: {sheet_row_number: row dict}; writes columns c1..c2 in contiguous runs."""
    cols = COLS[COLS.index(c1):COLS.index(c2) + 1]
    out, run = [], []
    for n in sorted(rows_by_num):
        if run and n != run[-1] + 1:
            out.append(run); run = []
        run.append(n)
    if run:
        out.append(run)
    return [{"range": f"cohort!{LETTER[c1]}{r[0]}:{LETTER[c2]}{r[-1]}",
             "values": [[cellv(rows_by_num[n], c) for c in cols] for n in r]} for r in out]


def stats(rows):
    w = sum(r["result"] == "W" for r in rows); l = sum(r["result"] == "L" for r in rows)
    v = sum(r["result"] == "VOID" for r in rows)
    units = round(sum((r["odds"] - 1) if r["result"] == "W" else -1 if r["result"] == "L" else 0 for r in rows), 2)
    ok = [r["clv_pct"] for r in rows if r["close_status"] == "ok" and r["clv_pct"] != ""]
    clv = round(sum(ok) / len(ok), 2) if ok else ""
    cov = round(100 * sum(r["close_status"] == "ok" for r in rows) / len(rows), 1)
    ids = len({r["pick_id"] for r in rows})
    return [len(rows), ids, w, l, v, units, clv, cov]


def plan_out(blocks, rows_after, summary, notes=()):
    plan = {"blocks": blocks, "expect": stats(rows_after), "summary": summary, "notes": list(notes)}
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    sys.exit(0 if blocks else 3)


def today_mel():
    return datetime.now(MEL).date()


# ---------------- slate ----------------
def cmd_slate(a):
    obj = json.load(open(a[0], encoding="utf-8"))
    rows = load_sheet(a[1])
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})", obj.get("date", ""))
    if not m:
        die("data.json date not parseable: " + str(obj.get("date")))
    d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
    iso = d.isoformat()
    engine = str(obj.get("engine") or "").strip() or die("data.json has no engine")
    have = {r["pick_id"] for r in rows}
    new, notes = [], []
    for g in obj.get("games", []):
        gid = g["id"]
        tip_utc = ""
        tm = re.fullmatch(r"(\d{1,2}):(\d{2})", str(g.get("tip", "")).strip())
        if tm:
            tip_utc = datetime(d.year, d.month, d.day, int(tm.group(1)), int(tm.group(2)), tzinfo=MEL) \
                .astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for p in g.get("picks", []):
            stat = str(p["s"]).upper()
            if stat not in MARKET:
                die(f"unknown stat {stat} for {p.get('p')}")
            alt = num(p["alt"])
            pid = f"{iso}|{gid}|{p['p']}|{stat}|{fmt(alt)}"
            if pid in have:
                notes.append("already in Sheet: " + pid); continue
            hm = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s*\((.*)\)\s*", str(p.get("h", "")))
            flags = " ".join(str(x) for x in p.get("f", []))
            um = re.search(r"\bU(\d+(?:\.\d+)?)\b", flags)
            sh = re.search(r"\((\d+/\d+)", str(p.get("safe_h", ""))) or re.search(r"(\d+/\d+)", str(p.get("safe_h", "")))
            team = str(p.get("t", "")).strip().upper()
            if not team:
                notes.append("no team on " + pid)
            r = {c: "" for c in COLS}
            r.update({"pick_id": pid, "date": iso, "tip_utc": tip_utc, "game": gid, "player": p["p"],
                      "team": team, "stat": stat, "std": num(p.get("std", "")), "alt": alt, "odds": num(p["o"]),
                      "hit_pct": num(hm.group(1)) if hm else "", "hit_sample": hm.group(2) if hm else str(p.get("h", "")),
                      "edge": num(p.get("e")), "stars": num(p.get("st")), "stake_u": num(um.group(1)) if um else "",
                      "flags": flags, "source_run": f"slate {iso}", "engine_version": engine,
                      "safe_alt": num(p.get("safe_alt", "")), "safe_odds": num(p.get("safe_o", "")),
                      "safe_hit_sample": sh.group(1) if sh else ""})
            if r["std"] == "":
                notes.append("no std on " + pid)
            new.append(r); have.add(pid)
    first = len(rows) + 2
    blocks = blocks_for({first + i: r for i, r in enumerate(new)}, "pick_id", "safe_hit_sample")
    plan_out(blocks, rows + new, f"{len(new)} new picks for {iso} ({engine})", notes)


# ---------------- capture ----------------
def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.\-']", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def get(url):
    with tempfile.TemporaryDirectory() as d:
        h, b = os.path.join(d, "h"), os.path.join(d, "b")
        r = subprocess.run(["curl", "-s", "-D", h, "-o", b, url], capture_output=True, text=True, timeout=60)
        if r.returncode:
            die("curl failed: " + r.stderr[:200])
        hdr = open(h, encoding="utf-8", errors="replace").read()
        try:
            body = json.load(open(b, encoding="utf-8"))
        except Exception:
            body = None
    return hdr, body


def cmd_capture(a):
    rows = load_sheet(a[0])
    pend = [(i + 2, r) for i, r in enumerate(rows)
            if r["odds"] != "" and r["close_status"] == "" and r["result"] == ""]
    if not pend:
        print(json.dumps({"message": "No picks awaiting a close - 0 requests"})); sys.exit(3)
    key = os.environ.get("ODDS_API_KEY") or die("ODDS_API_KEY not set")
    now = datetime.now(timezone.utc)
    _, events = get(f"{BASE}/events?apiKey={key}&dateFormat=iso")
    if not isinstance(events, list):
        die("events endpoint returned no parseable list")
    upcoming = {}
    for e in sorted(events, key=lambda e: e["commence_time"]):
        t = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        if t > now:
            upcoming.setdefault(f'{e["away_team"]}@{e["home_team"]}', (e, t))
    due = []
    for n, r in pend:
        try:
            away, home = (TEAMS[x.strip().upper()] for x in r["game"].split("@"))
        except (KeyError, ValueError):
            due.append((n, r, None, "name_mismatch")); continue
        hit = upcoming.get(f"{away}@{home}")
        if hit is None:
            if r["date"] <= today_mel().isoformat():
                due.append((n, r, None, "tipped_pre_capture"))
            continue
        ev, t = hit
        lead = t - now
        if lead > timedelta(minutes=MAX_LEAD):
            continue          # not closing yet - a later hourly run takes it
        if lead < timedelta(minutes=MIN_LEAD):
            continue          # too close to tip to price reliably; treated as tipped next run
        due.append((n, r, ev, None))
    if not due:
        print(json.dumps({"message": "No picks due for a close in this window - 0 requests", "pending": len(pend)}))
        sys.exit(3)
    need = {}
    for n, r, ev, pre in due:
        if ev is not None and pre is None:
            need.setdefault(ev["id"], set()).add(MARKET[r["stat"]])
    cache, remaining, used = {}, None, 0
    for eid, mks in need.items():
        hdr, body = get(f"{BASE}/events/{eid}/odds/?regions=au&bookmakers=pointsbetau&oddsFormat=decimal"
                        f"&markets={','.join(sorted(mks))}&apiKey={key}")
        used += len(mks)
        m = re.findall(r"x-requests-remaining:\s*(\d+)", hdr, re.I)
        if m:
            remaining = int(m[-1])
        cache[eid] = body if isinstance(body, dict) else None
    upd, results = {}, []
    for n, r, ev, pre in due:
        r = dict(r); status, close, clv = pre, "", ""
        if status is None:
            data = cache.get(ev["id"])
            if data is None:
                status = "api_error"
            else:
                mk = {m["key"]: m["outcomes"] for b in data.get("bookmakers", []) for m in b["markets"]}
                names = {o["description"] for v in mk.values() for o in v}
                hit = {x for x in names if x == r["player"]} or {x for x in names if norm(x) == norm(r["player"])}
                if not hit:
                    near = difflib.get_close_matches(norm(r["player"]), [norm(x) for x in names], n=1, cutoff=0.85)
                    status = "name_mismatch" if near else "pulled"
                else:
                    nm = next(iter(hit))
                    prices = [o["price"] for o in mk.get(MARKET[r["stat"]], [])
                              if o["description"] == nm and o["name"] == "Over" and abs(o["point"] - float(r["alt"])) < 1e-9]
                    if prices:
                        close, status = num(prices[0]), "ok"
                        clv = num(round((float(r["odds"]) / prices[0] - 1) * 100, 1))
                    else:
                        status = "line_gone"
        r["close_odds"], r["close_status"], r["clv_pct"] = close, status, clv
        upd[n] = r
        results.append(f'{r["player"]} {r["stat"]} {fmt(r["alt"])}: {fmt(close) or status}' + (f" (CLV {clv:+.1f}%)" if clv != "" else ""))
    after = list(rows)
    for n, r in upd.items():
        after[n - 2] = r
    oks = [r["clv_pct"] for r in upd.values() if r["clv_pct"] != ""]
    mean = f"{sum(oks)/len(oks):+.1f}%" if oks else "n/a"
    rem = remaining if remaining is not None else "?"
    warn = " ⚠️ <100 remaining" if isinstance(remaining, int) and remaining < 100 else ""
    flagged = [x for x in results if "CLV" not in x]
    summary = (f"Closes captured for {len(oks)}/{len(upd)} picks due. Mean CLV {mean}. "
               + (("Flagged: " + "; ".join(flagged) + ". ") if flagged else "")
               + f"{used} requests used. Odds API: {rem}/500 remaining.{warn}")
    plan_out(blocks_for(upd, "close_odds", "clv_pct"), after, summary, results)


# ---------------- resolve ----------------
def finished(r, now):
    t = r["tip_utc"]
    if t:
        tip = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return now >= tip + timedelta(hours=3, minutes=15)
    d = date.fromisoformat(r["date"])
    nm = now.astimezone(MEL)
    return d < nm.date() or (d == nm.date() and nm.hour >= 17)


def cmd_pending(a):
    rows = load_sheet(a[0]); now = datetime.now(timezone.utc)
    games = {}
    for r in rows:
        if r["result"] == "" and finished(r, now):
            games.setdefault((r["date"], r["game"]), []).append(
                {"pick_id": r["pick_id"], "player": r["player"], "stat": r["stat"], "alt": r["alt"]})
    out = [{"date_aest": d, "game": g, "us_date": (date.fromisoformat(d) - timedelta(days=1)).isoformat(), "picks": p}
           for (d, g), p in sorted(games.items())]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(0 if out else 3)


def cmd_resolve(a):
    rows = load_sheet(a[0]); acts = json.load(open(a[1], encoding="utf-8"))
    idx = {r["pick_id"]: i for i, r in enumerate(rows)}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    upd, notes = {}, []
    for pid, val in acts.items():
        if pid not in idx:
            notes.append("unknown pick_id: " + pid); continue
        r = dict(rows[idx[pid]])
        if r["result"] != "":
            notes.append("already settled, skipped: " + pid); continue
        if str(val).strip().upper() in ("DNP", "VOID"):
            r["actual"], r["result"] = "", "VOID"
        else:
            v = num(val)
            if v == "":
                notes.append(f"non-numeric actual for {pid}: {val}"); continue
            alt = float(r["alt"])
            r["actual"] = v
            r["result"] = "W" if v > alt else ("PUSH" if v == alt else "L")
        if r["close_status"] == "":
            r["close_status"] = "no_close"
        r["resolved_at"] = stamp
        upd[idx[pid] + 2] = r
    after = list(rows)
    for n, r in upd.items():
        after[n - 2] = r
    s = stats([r for r in upd.values()]) if upd else None
    summary = (f"Settled {len(upd)} picks: {s[2]}-{s[3]}" + (f", {s[4]} void" if s[4] else "") + f", {s[5]:+.2f}u") if upd else "Nothing settled"
    plan_out(blocks_for(upd, "close_status", "resolved_at"), after, summary, notes)


# ---------------- verify / report ----------------
def cmd_verify(a):
    plan = json.load(open(a[0], encoding="utf-8")); got = a[1:]
    if len(got) != 8:
        die("verify needs the 8 values from stats!B2:B9 in order")
    names = ["rows", "unique", "W", "L", "VOID", "units", "mean_clv", "coverage"]
    bad = []
    for nmv, e, g in zip(names, plan["expect"], got):
        ge = num(g)
        if e == "" and ge == "":
            continue
        if e == "" or ge == "" or abs(float(e) - float(ge)) > 0.011:
            bad.append(f"{nmv}: expected {e}, Sheet shows {g}")
    print(json.dumps({"ok": not bad, "mismatches": bad}))
    sys.exit(0 if not bad else 1)


def summarize(rows):
    s = [r for r in rows if r["result"] in ("W", "L")]
    w = sum(r["result"] == "W" for r in s); l = len(s) - w
    v = sum(r["result"] == "VOID" for r in rows)
    u = sum((r["odds"] - 1) if r["result"] == "W" else -1 for r in s)
    mo = sum(r["odds"] for r in s) / len(s) if s else 0
    be = 100 / mo if mo else 0
    hit = 100 * w / len(s) if s else 0
    return f"{w}-{l}" + (f" ({v} void)" if v else "") + (f", hit {hit:.1f}% vs B/E {be:.1f}% ({hit-be:+.1f}pp), {u:+.2f}u, ROI {100*u/len(s):+.1f}%" if s else "")


def cmd_report(a):
    rows = load_sheet(a[0])
    if len(a) > 1:
        plan = json.load(open(a[1], encoding="utf-8"))
        idx = {r["pick_id"]: i for i, r in enumerate(rows)}
        for b in plan["blocks"]:
            m = re.match(r"cohort!([A-Z]+)(\d+):([A-Z]+)(\d+)", b["range"])
            c1 = [c for c, L in LETTER.items() if L == m.group(1)][0]
            cols = COLS[COLS.index(c1):COLS.index(c1) + len(b["values"][0])]
            for k, vals in enumerate(b["values"]):
                n = int(m.group(2)) + k - 2
                if n >= len(rows):
                    rows.append({c: "" for c in COLS})
                for c, val in zip(cols, vals):
                    rows[n][c] = num(val) if c in NUMERIC and val != "" else val
    settled = [r for r in rows if r["result"]]
    if not settled:
        print("No settled picks."); return
    asof = max(r["date"] for r in settled)
    def win(nd):
        lo = max(SEASON_START, (date.fromisoformat(asof) - timedelta(days=nd - 1)).isoformat())
        return lo, [r for r in rows if lo <= r["date"] <= asof]
    out = [f"As of {asof} (flat 1u)"]
    for label, nd in (("7d", 7), ("30d", 30)):
        lo, rr = win(nd); out.append(f"{label} ({lo}..{asof}): {summarize(rr)}")
    out.append(f"Season: {summarize(rows)}")
    st = stats(rows); sett = sum(r["result"] in ("W", "L") for r in rows)
    nclv = sum(r["close_status"] == "ok" for r in rows)
    out.append(f"CLV: mean {st[6] if st[6] != '' else 'n/a'}% on n={nclv}; settled without a close: {sett - sum(r['close_status']=='ok' and r['result'] in ('W','L') for r in rows)}")
    out.append(f"Pending: {sum(r['result'] == '' for r in rows)}")
    print("\n".join(out))


def slate_iso(datestr):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})", datestr or "")
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date().isoformat()


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def cmd_done(a):
    cur = load_json(a[0]) if a else None
    today = today_mel().isoformat()
    done = set()
    if isinstance(cur, dict) and slate_iso(cur.get("date")) == today:
        done |= {g.get("id") for g in cur.get("games", []) if g.get("id")}
    if len(a) > 1 and os.path.exists(a[1]):
        try:
            done |= {r["game"] for r in load_sheet(a[1]) if r["date"] == today}
        except SystemExit:
            pass
    print(json.dumps({"date": today, "games_done": sorted(done)}))


def tip_key(g):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(g.get("tip", "")).strip())
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)


def cmd_merge(a):
    run = load_json(a[0])
    if not isinstance(run, dict) or not isinstance(run.get("games"), list):
        die("run.json missing or has no games list")
    for g in run.get("games", []):
        g.setdefault("engine", run.get("engine", ""))
    cur = load_json(a[1]) if len(a) > 1 else None
    if isinstance(cur, dict) and slate_iso(cur.get("date")) and slate_iso(cur.get("date")) == slate_iso(run.get("date")):
        ids = {g["id"] for g in run["games"]}
        keep = [g for g in cur.get("games", []) if g.get("id") not in ids]
        for g in keep:
            g.setdefault("engine", cur.get("engine", ""))
        games = sorted(keep + run["games"], key=tip_key)
        tot = {t[0]: t for t in cur.get("totals", []) if isinstance(t, list) and t}
        for t in run.get("totals", []):
            tot[t[0]] = t
        order = [g["id"] for g in games]
        totals = sorted(tot.values(), key=lambda t: order.index(t[0]) if t[0] in order else 999)
        out = dict(cur); out.update({k: v for k, v in run.items() if k not in ("games", "totals")})
        out["games"], out["totals"] = games, totals
    else:
        out = dict(run); out["games"] = sorted(run["games"], key=tip_key)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    cmd = {"slate": cmd_slate, "capture": cmd_capture, "pending": cmd_pending, "resolve": cmd_resolve,
           "verify": cmd_verify, "report": cmd_report,
           "done": cmd_done, "merge-data": cmd_merge}.get(a[0]) or die("unknown command " + a[0])
    cmd(a[1:])


if __name__ == "__main__":
    main()
