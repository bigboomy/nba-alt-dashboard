# NBA Alt-Over Dashboard

A single-page, mobile-first dashboard of daily stepped-down alt OVER player-prop picks, each with a price, hit rate and edge, plus a pace-based projected-totals table.

No build step, no dependencies, no tracking. Everything is in `index.html`.

## Publish it (2 minutes)

1. Create a GitHub repository and upload `index.html` and this `README.md`.
2. Repo **Settings → Pages → Build and deployment**: Source = *Deploy from a branch*, Branch = `main`, folder = `/ (root)`.
3. Share the URL GitHub gives you (`https://<user>.github.io/<repo>/`). It works on phones and follows light/dark mode.

## Update it daily

Edit only the JSON block marked `UPDATE ONLY THIS BLOCK EACH DAY` in `index.html`:

- `date`, `tz`, `book`, `quota`: header text
- `games[]`: one object per game with `id`, `tip`, `proj`, `mkt`, `ctx` and `picks[]` (empty array shows "No pick cleared the gates")
- pick fields: `p` player, `s` stat, `alt` line, `o` decimal odds, `h` hit rate text, `e` edge in percentage points, `st` stars (`"2.5"` shows ★★½), `opp` opponent allowance text, `f` flag chips
- `totals[]`: `[game, projected, market]`

Edge is `hit% − 1/odds`. Do not paste API keys or private page IDs into this repo.

## Disclaimer

Informational only, not betting advice. Hit rate is not profit, and prices and line-ups change. Gambling help (Australia): 1800 858 858.
