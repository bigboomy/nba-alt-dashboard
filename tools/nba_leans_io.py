#!/usr/bin/env python3
"""
nba_leans_io.py v1 - tracked-only leans (nba-session v3.9: rungs that pass at m=20 but not m=30).
Leans are NEVER staked: they live only in the `leans` tab and never touch cohort, stats or any
record / units / hit-rate figure. Deterministic: the model copies plan blocks into update_values calls.

Commands (leans.csv = fresh curl of the published leans tab; today.json = the dashboard data.json):
  pending leans.csv today.json            -> finished games with leans still unsettled (recorded or not yet)
  settle  leans.csv today.json lact.json  -> lplan.json: append today's new leans + settle any with actuals
                                             (lact.json = {lean_id: number|"DNP"}; {} just records)
  verify  lplan.json <6 values from lstats!B2:B7>   -> exit 0 match, 1 mismatch
  report  leans.csv                       -> rolling 7d / 30d / season line (flat 1u, notional)

lean_id = <date>|<AWY@HOM>|<player>|<stat>|<alt>   (same shape as a pick_id, but only ever in `leans`)
Exit codes: 0 = blocks to write / ok, 3 = nothing to do, 1 = error.
"""
import sys, json, csv, io, re
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

MEL = ZoneInfo("Australia/Melbourne")
SEASON_START = "2026-10-21"
TAB = "leans"
COLS = ("lean_id date tip_utc game player team stat std alt odds hit_pct hit_sample edge stars "
        "flags actual result resolved_at engine_version").split()
LETTER = {c: chr(65 + i) for i, c in enumerate(COLS)}
NUMERIC = {"std", "alt", "odds", "hit_pct", "edge", "stars", "actual"}
STATS = {"PTS", "REB", "AST"}


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
    return "" if v in ("", None) else (f"{v:g}" if isinstance(v, float) else str(v))


def load_tab(path):
    txt = open(path, encoding="utf-8").read()
    rows = list(csv.DictReader(io.StringIO(txt)))
    hdr = next(csv.reader(io.StringIO(txt)), [])
    if set(COLS) - set(hdr):
        die("leans header missing columns: " + ",".join(sorted(set(COLS) - set(hdr))) + " (fetch failed or tab not published?)")
    out = []
    for r in rows:
        d = {c: (r.get(c) or "") for c in COLS}
        if not d["lean_id"]:
            continue
        for c in NUMERIC:
            d[c] = num(d[c]) if d[c] != "" else ""
        out.append(d)
    return out


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def slate_date(s):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})", s or "")
    return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date() if m else None


def from_today(path):
    """Leans in today.json as sheet rows (not yet compared with the tab)."""
    obj = load_json(path)
    if not isinstance(obj, dict):
        return [], ["today.json unreadable - only already-recorded leans considered"]
    d = slate_date(obj.get("date"))
    if not d:
        return [], ["today.json date not parseable"]
    iso, out, notes = d.isoformat(), [], []
    for g in obj.get("games", []) or []:
        gid = str(g.get("id", ""))
        tm = re.fullmatch(r"(\d{1,2}):(\d{2})", str(g.get("tip", "")).strip())
        tip = datetime(d.year, d.month, d.day, int(tm.group(1)), int(tm.group(2)), tzinfo=MEL) \
            .astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if tm else ""
        eng = str(g.get("engine") or obj.get("engine") or "")
        for p in g.get("leans", []) or []:
            if not isinstance(p, dict):
                continue
            stat, alt, odds = str(p.get("s", "")).upper(), num(p.get("alt")), num(p.get("o"))
            if stat not in STATS or alt == "" or odds == "" or not p.get("p"):
                notes.append(f"skipped malformed lean in {gid}: {p.get('p')} {stat}"); continue
            hm = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s*\((.*)\)\s*", str(p.get("h", "")))
            r = {c: "" for c in COLS}
            r.update({"lean_id": f"{iso}|{gid}|{p['p']}|{stat}|{fmt(alt)}", "date": iso, "tip_utc": tip, "game": gid,
                      "player": str(p["p"]), "team": str(p.get("t", "")).upper(), "stat": stat,
                      "std": num(p.get("std")), "alt": alt, "odds": odds,
                      "hit_pct": num(hm.group(1)) if hm else "", "hit_sample": hm.group(2) if hm else str(p.get("h", "")),
                      "edge": num(p.get("e")), "stars": num(p.get("st")),
                      "flags": " ".join(str(x) for x in p.get("f", []) or []), "engine_version": eng})
            out.append(r)
    return out, notes


def finished(r, now):
    if r["tip_utc"]:
        return now >= datetime.fromisoformat(r["tip_utc"].replace("Z", "+00:00")) + timedelta(hours=3, minutes=15)
    d, nm = date.fromisoformat(r["date"]), now.astimezone(MEL)
    return d < nm.date() or (d == nm.date() and nm.hour >= 17)


def merged(a):
    rows = load_tab(a[0])
    have = {r["lean_id"] for r in rows}
    new, notes = [], []
    cand, n2 = from_today(a[1]) if len(a) > 1 else ([], [])
    notes += n2
    for r in cand:
        if r["lean_id"] not in have:
            new.append(r); have.add(r["lean_id"])
    return rows, new, notes


def stats(rows):
    w = sum(r["result"] == "W" for r in rows); l = sum(r["result"] == "L" for r in rows)
    v = sum(r["result"] == "VOID" for r in rows)
    u = round(sum((r["odds"] - 1) if r["result"] == "W" else -1 if r["result"] == "L" else 0 for r in rows), 2)
    return [len(rows), len({r["lean_id"] for r in rows}), w, l, v, u]


def cell(r, c):
    v = r[c]
    return "" if v in ("", None) else (v if c in NUMERIC else str(v))


def cmd_pending(a):
    rows, new, notes = merged(a)
    now = datetime.now(timezone.utc)
    games = {}
    for r in rows + new:
        if r["result"] == "" and finished(r, now):
            games.setdefault((r["date"], r["game"]), []).append(
                {"lean_id": r["lean_id"], "player": r["player"], "stat": r["stat"], "alt": r["alt"]})
    out = [{"date_aest": d, "game": g, "us_date": (date.fromisoformat(d) - timedelta(days=1)).isoformat(), "leans": p}
           for (d, g), p in sorted(games.items())]
    print(json.dumps({"games": out, "new_unrecorded": len(new), "notes": notes}, ensure_ascii=False, indent=1))
    sys.exit(0 if out or new else 3)


def cmd_settle(a):
    if len(a) < 3:
        die("settle needs leans.csv today.json lact.json")
    rows, new, notes = merged(a[:2])
    acts = load_json(a[2])
    if not isinstance(acts, dict):
        die("lact.json is not a JSON object")
    allr = rows + new
    idx = {r["lean_id"]: i for i, r in enumerate(allr)}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    touched, settled = set(), []
    for lid, val in acts.items():
        if lid not in idx:
            notes.append("unknown lean_id: " + lid); continue
        i = idx[lid]; r = dict(allr[i])
        if r["result"] != "":
            notes.append("already settled, skipped: " + lid); continue
        if str(val).strip().upper() in ("DNP", "VOID"):
            r["actual"], r["result"] = "", "VOID"
        else:
            v = num(val)
            if v == "":
                notes.append(f"non-numeric actual for {lid}: {val}"); continue
            r["actual"] = v
            r["result"] = "W" if v > float(r["alt"]) else ("PUSH" if v == float(r["alt"]) else "L")
        r["resolved_at"] = stamp
        allr[i] = r; touched.add(i); settled.append(r)
    blocks = []
    # settled rows already in the tab: write actual..resolved_at only
    old = sorted(i for i in touched if i < len(rows))
    runs, cur = [], []
    for i in old:
        if cur and i != cur[-1] + 1:
            runs.append(cur); cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    cols = COLS[COLS.index("actual"):COLS.index("resolved_at") + 1]
    for run in runs:
        blocks.append({"range": f"{TAB}!{LETTER['actual']}{run[0] + 2}:{LETTER['resolved_at']}{run[-1] + 2}",
                       "values": [[cell(allr[i], c) for c in cols] for i in run]})
    # new rows (settled or not): append whole rows
    if new:
        first = len(rows) + 2
        blocks.append({"range": f"{TAB}!A{first}:{LETTER[COLS[-1]]}{first + len(new) - 1}",
                       "values": [[cell(allr[len(rows) + k], c) for c in COLS] for k in range(len(new))]})
    s = stats(settled)
    summary = (f"{len(new)} new leans recorded; settled {len(settled)}"
               + (f": {s[2]}-{s[3]}" + (f", {s[4]} void" if s[4] else "") + f", {s[5]:+.2f}u notional" if settled else ""))
    print(json.dumps({"blocks": blocks, "expect": stats(allr), "summary": summary, "notes": notes}, ensure_ascii=False, indent=1))
    sys.exit(0 if blocks else 3)


def cmd_verify(a):
    plan = load_json(a[0]) or die("lplan.json unreadable")
    got = a[1:]
    if len(got) != 6:
        die("verify needs the 6 values from lstats!B2:B7 in order")
    bad = []
    for nm, e, g in zip(["rows", "unique", "W", "L", "VOID", "units"], plan["expect"], got):
        ge = num(g) if str(g).strip() != "" else 0
        if abs(float(e) - float(ge or 0)) > 0.011:
            bad.append(f"{nm}: expected {e}, Sheet shows {g}")
    print(json.dumps({"ok": not bad, "mismatches": bad}))
    sys.exit(0 if not bad else 1)


def line(rows):
    s = [r for r in rows if r["result"] in ("W", "L")]
    if not s:
        return "no settled leans"
    w = sum(r["result"] == "W" for r in s)
    u = sum((r["odds"] - 1) if r["result"] == "W" else -1 for r in s)
    imp = 100 * sum(1 / r["odds"] for r in s) / len(s)
    hit = 100 * w / len(s)
    return f"{w}-{len(s) - w} (n={len(s)}), hit {hit:.1f}% vs implied {imp:.1f}% ({hit - imp:+.1f}pp), {u:+.2f}u notional"


def cmd_report(a):
    rows = load_tab(a[0])
    sett = [r for r in rows if r["result"]]
    if not sett:
        print("Leans: no settled leans yet."); return
    asof = max(r["date"] for r in sett)
    out = [f"Leans as of {asof} (tracked only, 0u staked; notional 1u)"]
    for lab, nd in (("7d", 7), ("30d", 30)):
        lo = max(SEASON_START, (date.fromisoformat(asof) - timedelta(days=nd - 1)).isoformat())
        out.append(f"{lab}: " + line([r for r in rows if lo <= r["date"] <= asof]))
    out.append("Season: " + line(rows))
    print("\n".join(out))


def main():
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    cmd = {"pending": cmd_pending, "settle": cmd_settle, "verify": cmd_verify, "report": cmd_report}.get(a[0]) \
        or die("unknown command " + a[0])
    cmd(a[1:])


if __name__ == "__main__":
    main()
