---
name: trader-entry
description: >-
  Entry Timer for NSE stocks. Watches a setup bar by bar and tells you the
  moment to get in. Reads 1-minute or 5-minute bars (Kite files, Yahoo as fallback),
  builds the opening range (the first 15 minutes, 9:15 to 9:30 IST, by
  default) and calls the entry on the first completed bar that CLOSES above
  the range high, or above a level you give it (like a 20-day high from the
  scanner), with an optional volume check. Says waiting and how far,
  triggered with the time and the price, expired, or not open yet, and
  writes trader/entry.json for /trader-exit. Long side only. It never places
  an order. Use when the user says "tell me when to get in", "watch
  RELIANCE", "has it broken out", "did it break the opening range", "entry
  signal", "time my entry", "check my watchlist", or after /trader-scanner
  and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---
> **Data source: Kite.** Candles come from the Kite connector via `market.py kite-import` into `trader/kite/` (see trader-market); this tool reads that folder when `--offline` is not given. With no Kite file it falls back to Yahoo (`INDIA_ALLOW_YAHOO=0` turns that off). Kite's intraday history stops at 15:15.


# trader-entry

## Job and boundaries
- **Does:** waits for the first completed bar to close above the opening-range high (or a level the user gives) and says waiting, triggered with time and price, or expired; long side only; writes `trader/entry.json`.
- **Does not:** place or size orders, set the stop, count a bar that has not closed, call an entry early, or move the level afterwards.
- **Needs first:** bars for today and a stock that `/trader-news` and `/trader-research` did not rule out. `check` prints today's research verdict under the status; SKIP or no verdict is said to the user before going on, and automation will not enter without CLEAR; not locked at its upper circuit.
- **Hands off to:** `/trader-exit`, before anything is bought.
- **Stop and ask the user when:** the trigger is more than one bar old (a late entry is a different trade), the stock is at its upper circuit, or the user wants a short.


The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Intraday trading loses
most people money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write          # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1490.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:30    # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                       # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--offline FILE` (a
saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder of
`<TICKER>.json`), `--now "2026-10-26 10:15"` (India time) to replay a day.
`watch` with `--now` or `--offline` runs on a simulated clock and replays the
day bar by bar without waiting. Symbols are NSE tickers (`M&M`, `BAJAJ-AUTO`
work; `.NS` is added for Yahoo).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:30), IST. Optional |
| Yahoo chart API | today's bars, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range |

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level
  to beat is the range high. (The NSE opens at 9:15 after a pre-open call
  auction from 9:00 to 9:08; the first minutes are wild, so the earliest entry
  default is 9:30.)
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:30 bar closes at 9:35 AM).
  The price is its close, on the Rs 0.05 tick.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment. A live tick
  is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average.
- **Time limits.** No entries before 9:30 or after 14:30 (config). That late
  cutoff leaves room before the 15:10 flat-by.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. While the range
  forms: say so. Weekends and NSE holidays: the market is closed.
- **Data lag.** Yahoo's India intraday bars can run a few minutes behind. The
  tool says how old the last bar is; if a minute matters, check the price in the
  Kite connector (`get_quotes`) before acting.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:30` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example (the repo's offline test fixture: synthetic bars, not real prices)

RELIANCE, Friday 23 Oct 2026, 5-minute bars:

```
RELIANCE  Fri 2026-10-23 09:36 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹1478.00, low ₹1473.20 (3 of 3 bars).
TRIGGERED 9:35 AM at ₹1483.15: the 9:30 bar (bar 3) closed above the range high ₹1478.00 (+0.35%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

Run again at 9:52 and it adds "That was 17 minutes ago ... A late entry is a
different trade."

## Rules

- Bar by bar, on free Yahoo bars. They are unofficial and can lag. Say so
  when a minute matters.
- A signal is information, not an order, and not a promise.
- Long side only. The plans here are long cash-segment trades. If the user wants
  a short, say that intraday short selling needs MIS and extra care, and is off.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.
- Check the stock is not locked at its upper circuit: a buy there may never fill.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/trader-broker` from a background
watch: nobody is there to say yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Intraday
  trading loses money for most people, and SEBI studies of Indian retail traders say the same.
- Practice mode by default (`"paper": true`): Zerodha has no paper account, so in this mode tickets are
  prepared and the yes is recorded but Claude does not call `place_order`. Going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat (or, only on a day the user switched automation on in their own words, the order passes `ticket.py auto-approve`), and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- Cash-segment stocks and ETFs only: no futures, no options, no margin and no short selling unless the
  config allows it and the user confirms.
- No passwords, tokens or API secrets in the chat. The Kite login is done by the user on Zerodha's page.
