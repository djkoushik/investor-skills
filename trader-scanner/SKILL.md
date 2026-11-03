---
name: trader-scanner
description: >-
  Chart Scanner for the NSE. Scans hundreds of charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the list of NSE
  equities from NSE's own files (Nifty 500 by default, or Nifty 50 / 100 /
  200, Midcap 150, Smallcap 250, or every EQ-series share), runs a pre-market
  scan of daily closes (20-day closing-high breakout, bull flag, reclaimed
  the 20-day average, oversold, oversold bounce) and an intraday
  opening-range breakout scan on 5-minute bars, flags stocks near their
  circuit limit, then writes trader/watchlist.json for trader-news and
  trader-entry. Kite candles (Yahoo as fallback), bar by bar. Use when the user
  says "scan the market", "find me setups", "what is breaking out", "opening
  range breakouts", "scan for bull flags", "oversold stocks", "pre-market
  scan", "build my watchlist", "what should I look at today", or at the start
  of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--index nifty500] [--at HH:MM]"
---
> **Data source: Kite.** Candles come from the Kite connector via `market.py kite-import` into `trader/kite/` (see trader-market); this tool reads that folder when `--offline` is not given. With no Kite file it falls back to Yahoo (`INDIA_ALLOW_YAHOO=0` turns that off). Kite's intraday history stops at 15:15.


# trader-scanner

## Job and boundaries
- **Does:** lists charts that match a rule (20-day breakout, bull flag, MA20 reclaim, oversold, oversold bounce, opening-range breakout) from the Nifty universe and writes `trader/watchlist.json`.
- **Does not:** pick a stock, call anything a buy, size a position, read news or fundamentals, or round a partial scan up to 'the whole market'.
- **Needs first:** a trading day; for `orb`, the first bar after the 15-minute range has closed (9:35 IST or later) and not past `no_entries_after`.
- **Hands off to:** `/trader-research` and `/trader-news` for the stocks the user picks, then `/trader-entry`.
- **Stop and ask the user when:** fetched charts are far fewer than the universe, there is no Kite or Yahoo data, or the market is closed.


The Chart Scanner is the first step of an intraday trade. It reads hundreds
of free charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who trade intraday lose
money; the scanner does not change that. It only saves you from flipping
through 500 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                        # Nifty 500 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty100       # or nifty50|nifty200|midcap150|smallcap250|all
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                           # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,INFY,M\&M   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                             # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                  # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` and `--min-volume` (daily 500,000 shares, orb 0),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2), `--until HH:MM` (no new
hits after this; default 14:30), `--now "2026-10-26 10:15"` (India time) to
pin the clock, `--offline PATH` to read saved files, `--no-write`, `--show N`,
`--json`. Exit code 0 when the scan found something, 1 when it found nothing or
could not run (the reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |

## The universe

`universe` downloads NSE's index constituent file (`ind_nifty500list.csv`
from nsearchives.nseindia.com) or, with `--index all`, the full equity list
(`EQUITY_L.csv`). It keeps SERIES `EQ` shares (the normal rolling-settlement
series; T-group, BE and trade-for-trade stocks cannot be traded intraday) and
drops ETFs-as-bonds, SME and debt. NSE sends a 403 to plain scripts, so the
tool sends a browser User-Agent. Zerodha's MIS list is narrower than NSE's EQ
list (some stocks are delivery-only): check the stock when you pick it, the
Kite connector's `search_instruments` helps. The default Nifty 500 is a good
start: liquid names tight on spreads.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 500
symbols take about 25 requests with a polite pause between. Liquidity comes from the
spark meta: last price at least `--min-price` (Rs 50) and last session's
volume at least `--min-volume`. Run it before the open or after the close:
during the session the volume is the day's so far, and today's daily bar is
left out.

The setups are computed from daily closes only:

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Each daily hit also carries its change on the day and a `circuit_band` when the
move is within half a point of a 2 / 5 / 10 / 20 % price band: a stock that has
run close to its circuit may lock there, and a buy or a stop can fail to fill.
Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition, so
   no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. Stage 2 checks at most `--max-confirm` (200) candidates,
   the most traded first, and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

Each hit shows the gap from the previous close (a stock that gapped up 4 % has
already made its move) and the circuit band if it is near one. A chart is
usable only when every 5-minute bar from 9:15 to the cutoff is there; a
missing bar means a halt or a thin stock. A bar counts only once it is
complete. Before 9:35 AM, on a weekend or NSE holiday, after the close, or
after `no_entries_after` (an expired setup) it prints one plain line and
writes nothing.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag, and for
  Indian stocks the intraday bars may come several minutes late. Bars are
  5-minute bars, never tick by tick. If a minute matters, check the price in the
  Kite connector (`get_quotes`).
- Partial results are said as partial: "Fetched 480 of 500 charts (20
  failed or had no data)". Never round a partial scan up to "the whole market".
- The NSE holiday list covers 2026. For other years add dates to
  `trader/holidays.json` (see trader-alerts).
- Not covered: pre-open call-auction data, delivery percentages, F&O data and
  analyst or fundamental views. For fundamentals and analyst views use
  Tickertape (see the README): this scanner only reads charts.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST, when the first bar past the 15-minute range has closed (10:00 to 10:15 is the classic): `scan.py orb`. Tell the
   user how many usable charts broke their range and when, and that a later hit
   is closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example (the repo's offline test fixture: synthetic bars, not real prices)

Friday 23 Oct 2026, opening-range scan at 10:15 AM over a six-stock fixture:

```
Opening-range breakout scan, Fri 2026-10-23 10:15 AM IST: 15-minute range (9:15 to 9:30 AM), 5-minute bars, Yahoo.
Fetched 5 of 6 charts; 5 usable; 5 candidates, 5 confirmed with real highs and lows.
4 of 5 charts closed above their opening-range high by 10:15 AM:
   9:35 AM  RELIANCE        ₹1,475.15  range 1,465.90 to 1,470.00  +0.35% over  gap -0.0%
   9:35 AM  TCS             ₹3,936.20  range 3,911.70 to 3,931.50  +0.12% over  gap +0.4%
   9:40 AM  M&M             ₹3,023.70  range 3,004.30 to 3,011.05  +0.42% over  gap -0.0%
   9:55 AM  INFY            ₹1,498.40  range 1,493.35 to 1,496.20  +0.15% over  gap -0.3%
```

## Rules

- Say what the scan is: charts matching a rule. Never "stocks to buy".
- Say how many charts were fetched, usable and scanned, every time.
- A later hit is closer to the price now: say the time of the breakout.
- Never pick the stock for the user.

## What it never does

It never places an order, never sizes a position, and never reads anything
that needs a login.

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
