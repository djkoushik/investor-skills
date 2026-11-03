---
name: trader-candles
description: Candlestick pattern finder for Indian stocks. Reads OHLC bars (Kite historical data, a CSV, or Yahoo bars) and lists hammer, hanging man, shooting star, doji, engulfing, harami, piercing line, dark cloud, morning/evening star, three white soldiers/black crows, tweezers, inside/outside bars, kickers, belt holds, abandoned baby, three inside/outside, gap patterns and continuation patterns (about 55 in all), with the trend before each, and can show how each pattern actually fared in that stock's own history. Use when the user says "candlestick", "any patterns on X", "is that a hammer", "check the chart".
---

# Trader Candles (India)

## Job and boundaries
- **Does:** finds candlestick patterns in OHLC bars with the trend before each, volume ratio, a confirmation level and a stop level, and shows how each fared in that stock's own history (`stats`).
- **Does not:** give a signal, claim an edge from fewer than about 30 cases, include costs in `stats`, or use close-only data.
- **Needs first:** oldest-first bars with open, high, low, close, volume (Kite JSON or CSV).
- **Hands off to:** `/trader-entry` or `/trader-swing` for the actual setup; `/trader-budget` and `/trader-risk` before any trade.
- **Stop and ask the user when:** bars have no open/high/low, or the pattern sits on 1-5 minute bars (say it is mostly noise).


Hints, not signals. Single candlestick patterns have weak, unstable edge. This skill finds them, says what they need to be meaningful (the trend before, a level, volume), and with `stats` shows how often they worked on this stock's own bars so the user can see for themselves.

## Get bars
Oldest first, columns date, open, high, low, close, volume.
- Zerodha: the Kite connector's historical data tool (needs the Kite connection). Save its result as JSON; `{"candles": [[date,o,h,l,c,v], ...]}` is accepted as it is.
- Otherwise a CSV with those columns, e.g. exported from the chart source the user trusts.
- Tickertape history has closes only (no open/high/low) and cannot be used for candles.

## Run
```
python3 trader-candles/candles.py scan bars.json --last 5 --timeframe 15min
python3 trader-candles/candles.py stats bars.json --horizon 5     # after-the-pattern moves in this file
python3 trader-candles/candles.py list                            # the rules and thresholds
```

## Reading the output
- Bias BULLISH / BEARISH is given only when the pattern has the trend it needs (a hammer after a fall; the same shape after a rise is a hanging man). Otherwise the bias is NEUTRAL.
- Volume ratio compares the bar with its previous 20: a pattern on 2x volume means more than one on thin volume.
- Always say what would confirm it (the next bar closing beyond the pattern) and where the stop would sit (beyond the pattern's extreme). Then hand over to `/trader-budget` and `/trader-risk` as usual.
- With `stats`, small counts (n under about 30) mean nothing and hit rates near 50% mean no edge. Costs are not included. Say this plainly; do not oversell a pattern.

## Timeframe and trend
Patterns on a daily or weekly chart carry more weight than on 1-5 minute bars. Patterns need a trend before them: in a sideways band they mean little unless they sit at the breakout point. There is no fixed rule for how long the trend must be; the tool uses the last 5 closes (`trend_bars`), and more bars are generally better. Wait for the pattern bar to close before acting. Filter output with `--bias bullish|bearish` or `--pattern engulfing`.

## Limits
- Rising/falling methods look for exactly three small bars (some sources allow three or more), and tweezers only match consecutive bars (some allow gaps between them).
- The rules are common textbook definitions with fixed thresholds (see `list`); other books draw the lines differently.
- It does not know support/resistance, news or the market's direction. Pair it with `/trader-research` and `/trader-news`.
- Works on any timeframe, but patterns on 1-5 minute bars are mostly noise.

## Safety
Practice mode by default. Education and tooling, not financial advice. No F&O. Never ask for or paste credentials in chat.
