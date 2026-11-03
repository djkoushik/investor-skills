---
name: trader-swing
description: Swing trading for Indian stocks (hold days to weeks). Technicals decide the entry, stop and targets: daily trend, support/resistance zones, next-session pivot levels, breakout volume. Results dates and weak markets only shrink the size; weak fundamentals are a note, not a veto. Use for "swing trade X", "is X at support", "where is the stop/target", "give me a swing plan". Self-contained; needs daily bars from Kite or a CSV.
---

# Trader Swing (India)

## Job and boundaries
- **Does:** turns daily-bar structure into one plan: entry, stop just beyond the level that must hold, targets, reward:risk and size; also screens a folder of bars and tracks open swing trades.
- **Does not:** veto on weak fundamentals (a note only), place orders, or force a setup when there is none.
- **Needs first:** 6+ months of daily bars, the stock's tick, a max loss and budget; optional index bars and facts.json.
- **Hands off to:** the user places the entry and stop by hand in Kite (stop-loss or GTT); trader-broker and trader-risk are same-day only.
- **Stop and ask the user when:** ASM/GSM or default risk appears (the plan stops), or a results date falls within 10 days of a planned hold.


**What matters here:** price structure. Support and resistance zones, the daily trend, a bullish bar at support or a high-volume breakout, and a stop that sits just beyond the level that must hold. **What only matters a little:** a results date or a weak market (they shrink the size or raise the reward:risk bar). **What is a note, not a veto:** weak fundamentals. A stock at a crucial support can be a valid swing trade even if the business is weak: keep the stop strict and never average down. **What does stop the plan:** ASM/GSM listing or default risk.

Stands alone: `swing.py` and `ta.py` in this folder are all it needs.

## Inputs
- **Daily bars** (6+ months): Kite `get_historical_data` interval `day`, or a CSV (date,open,high,low,close,volume).
- Optional **index bars** (Nifty 50, same format): `--index nifty.json`. A falling index halves the size and raises the minimum reward:risk to 2.0.
- Optional **facts.json**: `fundamentals` (CANDIDATE/WATCH/AVOID from a fundamentals read, if the user has one), `red_flags`, and `events` (results or board meeting dates; read them from Tickertape `in_stock_corporate_read`). The shape is at the top of `swing.py`.
- `--tick` the stock's tick size (Kite instrument search, e.g. 0.1 for RELIANCE); default 0.05.

## Run
```
python3 trader-swing/swing.py scan bars.json --symbol RELIANCE --index nifty.json --facts facts.json --max-loss 2000 --budget 200000 --tick 0.1
python3 trader-swing/swing.py rules
```
It prints the trend, support and resistance zones, the next session's pivot levels (S2, S1, P, R1, R2), and the best setup if there is one: entry, stop, targets, reward:risk and the size. It saves the plan to `trader/swing.json`.

## Reading the result
- **PLAN**: say the setup in words ("bounce off support 99 to 99 that held 6 times"), the entry, the stop, the first target, reward:risk, and the size. Remind the user the stop is a daily-close rule unless they place a stop-loss order or GTT in Kite.
- **NO PLAN**: say why (not at a held zone, no bullish bar, reward:risk too low, or a red flag). Do not force a setup.
- Counter-trend plans (daily trend down) get half size and need reward:risk of at least 2.
- Day-wise levels (the pivots) are for fine-tuning entries and exits inside the plan, not setups by themselves.

## Daily routine for an open swing trade
After the close: has price closed below the stop? Has the first target been hit (move the stop to the entry)? Has a results date come within 10 days (exit or halve)? That is all.

## Limits
- It does not place orders. `trader-broker` and `trader-risk` are built for same-day trades; place the entry and the stop by hand in Kite (a stop-loss or GTT order).
- Delivery charges (STT on both sides, DP charge) are not included; they are small against the risk but not zero.
- Support and resistance are probabilities, not guarantees. A gap can jump a stop.

## Safety
Education and tooling, not financial advice. No credentials in chat.

## Added: patterns, confirmation, screening, trade tracking

`scan` now also reports **chart patterns** (double top/bottom, head and shoulders and its inverse, ascending/descending triangle, bull/bear flag, each "forming" or "confirmed" with the trigger level) and **confirmation**: MACD side and crosses, Bollinger squeeze, up-volume versus down-volume over 20 bars, and the candlestick patterns on the last two bars (via the bundled `candles.py`, a copy of trader-candles kept identical by a test). These appear in the notes; they do not change the entry, stop or targets, which still come from support/resistance and the reward:risk rule. Pattern detection on noisy charts is approximate: look at the chart.

`python3 swing.py screen BARS_DIR [--index nifty.csv] [--top 10]` ranks the files in a folder (one `SYMBOL.csv` each) after dropping illiquid names (average 20-day turnover under Rs 5 crore, change with `--min-turnover`). Points for uptrend, beating the index over 20 days, shrinking volatility and tight Bollinger bands. It makes a shortlist to chart, not a buy list.

`track add SYMBOL --entry --stop --target [--target] [--max-days 15]`, `track check --bars-dir DIR`, `track close SYMBOL --exit-price`, `track list`. `check` says STOP (daily close below the stop: exit, do not turn it into an investment), TARGET (take part, move the stop to the entry), TIME (stale) or HOLD. `close` records the result in R. Saved in `trader/swing_trades.json`.
