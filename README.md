# NBA Alt-Over Dashboard

A single-page, mobile-first dashboard of daily stepped-down alt OVER player-prop picks, each with a price, hit rate and edge, plus a pace-based projected-totals table.

No build step, no dependencies, no tracking. The page is `index.html`; the content lives in `data.json`.

## Publish with GitHub Pages

1. Create a GitHub repository and upload `index.html`, `data.json` and this `README.md` to the `main` branch.
2. Go to **Settings > Pages**, set Source to **Deploy from a branch**, Branch to `main`, folder `/ (root)`, and save.
3. Share the URL GitHub gives you (`https://<user>.github.io/<repo>/`). It works on phones and follows light/dark mode.

## Daily updates

The daily job rewrites only `data.json`. It never edits `index.html`. The page fetches `data.json` on every load (cache disabled), so a new commit shows up as soon as Pages redeploys.

If `data.json` is missing or invalid the page shows "Could not load today's data". If `generated` is more than 20 hours old, an amber "may be out of date" banner is shown.

## data.json schema

Top level (object):

| Field | Type | Notes |
|---|---|---|
| `generated` | string | ISO 8601 timestamp with offset, e.g. `"2026-10-29T06:00:00+11:00"` |
| `date` | string | Display date, e.g. `"Thu 29 Oct 2026"` |
| `tz` | string | Time zone label, e.g. `"Melbourne time (AEDT)"` |
| `book` | string | Bookmaker and odds format label |
| `games` | array of game | One per target game |
| `totals` | array of rows | One per game on the slate |

Game object:

| Field | Type | Notes |
|---|---|---|
| `id` | string | `"AWY@HOM"` |
| `tip` | string | Tip-off time, e.g. `"13:40"` |
| `proj` | number | Projected game total |
| `mkt` | number | Market total |
| `ctx` | string | Short context line |
| `picks` | array of pick | May be empty (shows "No pick cleared the gates") |

Pick object:

| Field | Type | Notes |
|---|---|---|
| `p` | string | Player name |
| `s` | string | Stat, e.g. `"PTS"`, `"AST"` |
| `alt` | number | Alt OVER line, e.g. `5.5` |
| `o` | number | Decimal odds, e.g. `1.69` |
| `h` | string | Hit rate text, e.g. `"80% (8/10)"` |
| `e` | number | Edge in percentage points, e.g. `20.8` |
| `st` | string | Stars as a string, e.g. `"2.5"` shows two and a half stars |
| `opp` | string | Opponent allowance text; `"—"` if none |
| `f` | array of string | Short uppercase chips, e.g. `["EARLY","B2B"]`; may be empty |

Totals row: `[game, proj, mkt]` as `[string, number, number or null]`. Use `null` for `mkt` when there is no market line; the page shows "—" for Mkt and Gap.

The page skips malformed picks and rows rather than failing, but keep the schema exact.

## Local preview

`fetch` does not work from `file://`. Use a local server from this folder:

```
python3 -m http.server
```

Then open http://localhost:8000/.

## Disclaimer

Informational only, not betting advice. Hit rate is not profit, and prices and line-ups change. Gambling help (Australia): 1800 858 858.
