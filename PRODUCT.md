# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Mitch, plus a few mates who follow the picks. All of them know the vocabulary: alt over, rung, leans, HR80, PIN, LSR, OUTLIFT. Nobody else is a target reader, even though the GitHub Pages URL is public.

## Product Purpose

A daily read-out of the NBA alt-over pipeline: tonight's stepped-down alt OVER player props (picks and leans), each with price, hit rate, edge and a plain-English verdict, plus the matchup context behind them and the running record.

Two jobs, in this order:
1. **Decide what to bet before tip.** Scan tonight's picks and leans, check the matchup, place bets.
2. **Track results and tiers.** See the record, hit rates, and whether each 0u tier test (HR80, PIN, LSR, verdict) is heading for CONFIRM or KILL.

Success: a reader can tell in seconds which bets are worth taking tonight and why, and can trust the record without checking the Sheet.

## Positioning

A personal model's output, not a tipster site. Every bet on the page is something the pipeline priced and gated, and every claim traces to data (Odds API prices, box scores, Pinnacle, team ranks). The page shows what the model did, misses included. It never sells a pick.

## Operating Context

- Opened on phones and on desktop. Phone for quick checks near tip (picks land about 90 minutes before each tip, first around 09:30 Melbourne time). Desktop for digging into the record.
- The pipeline on the home server runs on schedule and commits only `data.json` and the sibling JSON files (`record.json`, `schedule.json`, `health.json`, `absence.json`, `changelog.json`). The page fetches them on every load.
- Times are Melbourne time (AEDT); odds are Pointsbet AU decimal.
- Results settle after the games finish (about 6pm on most days).

## Capabilities and Constraints

- Static site on GitHub Pages: `index.html` with inline CSS/JS, no build step, no dependencies, no tracking. It has a PWA manifest and a service worker.
- **Display only.** The page never changes a gate, tier, stake, price or the 3-lean cap. Betting rules are Mitch's alone, and each rule change gets a version bump, a changelog entry and a golden test in the pipeline.
- The Google Sheet is the record of truth; the page mirrors it and never rewrites settled history.
- Freeze until 27 Nov 2026: no new tiers or gate edits; data and display fixes only.
- New data fields need a pipeline change first. Until the next slate run, the page must hide what the current data lacks rather than show it empty.
- Terminology is fixed: picks (staked) versus leans (tracked at 0u), rung, alt line, tier flags (HR80, PIN, LSR; HR85 and OUTLIFT are retired but appear in history).

## Brand Commitments

Plain English over codes: every lean carries a verdict (Worth a look / Mixed / Caution) and one sentence. Codes stay available but secondary. Write for Mitch's mates as well as Mitch.

## Evidence on Hand

- Live data: `data.json` (tonight's slate), `record.json` (settled history), `changelog.json` (every rule and display change), `health.json`, `schedule.json`, `absence.json`.
- No testimonials, ROI marketing claims or outside endorsements exist. Never fabricate them.

## Product Principles

1. **Tonight first.** The pre-tip decision outranks everything else on first load.
2. **Show the evidence, not the hype.** Hit counts, prices and the record carry the page; a reader can always see why a bet is there.
3. **Honest about misses.** Losses, killed tiers and caution verdicts are as visible as wins.
4. **Never imply a rule the pipeline doesn't have.** Display only; nothing on the page changes the bet.
5. **Readable on a phone at a glance, deep on desktop.**
