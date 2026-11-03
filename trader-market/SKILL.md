---
name: trader-market
description: >-
  The Indian market profile shared by every trader-* skill: NSE hours (9:15 to
  15:30 IST), the exchange holiday list, India Standard Time, the Rs 0.05
  tick, NSE ticker rules and the Yahoo / Kite symbol names, circuit-band hints
  and Zerodha's intraday charges (brokerage, STT, exchange fees, GST, stamp
  duty). Use when the user asks "is the NSE open", "when does the market
  open", "what does a round trip cost", "what is the breakeven", "is that a
  valid NSE symbol", "add a holiday", or when another trader-* tool needs the
  market facts. Other skills import it; it is not a step in the trade.
argument-hint: "[clock | costs | symbol | holidays]"
---

# trader-market

## Job and boundaries
- **Does:** holds the NSE clock, holiday list, tick sizes, symbol rules, circuit limits and Zerodha charges for every other skill, and answers 'is the NSE open', 'what does a round trip cost', 'is this a valid symbol'.
- **Does not:** scan, rank, advise, size or trade; it is a shared library, not a step in a trade.
- **Needs first:** nothing, except `trader/holidays.json` for any year after 2026 and `instrument add` for each stock's own tick and circuits.
- **Hands off to:** every other skill imports it.
- **Stop and ask the user when:** a date falls outside the holiday list's years, or a stock has no saved instrument (tick then defaults to Rs 0.05 and must be said as an assumption).


One file, `market.py`, holds everything that is "the market" and not "the
method", so every tool agrees on the time and on what a trade costs. The other
tools find it at `../trader-market/market.py`, so keep this folder next to them.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/market.py" clock                              # is the NSE open? next open
python3 "${CLAUDE_SKILL_DIR}/market.py" clock --now "2026-10-26 09:20"
python3 "${CLAUDE_SKILL_DIR}/market.py" costs --buy 1424.60 --sell 1433.40 --qty 20
python3 "${CLAUDE_SKILL_DIR}/market.py" symbol "M&M" reliance.ns NSE:INFY
python3 "${CLAUDE_SKILL_DIR}/market.py" holidays                           # the calendar the tools use
```

## Facts it encodes (verify before relying on them: they change)

- NSE cash segment: pre-open order entry 9:00 to 9:08, normal market 9:15 to 15:30
  IST (UTC+5:30, no daylight saving). Muhurat trading on Diwali is a special evening session
  whose hours NSE announces late: the tool treats it as closed unless you add it as a special session.
- Holidays: the 2026 NSE trading-holiday list is built in. For 2027 or any change, create
  `trader/holidays.json`:
  `{"years": [2027], "holidays": {"2027-01-26": "Republic Day"}, "special_open": {"2027-11-01": "Muhurat"}}`.
  The tools say "the holiday list does not cover this year" until you do.
- Tick size: Rs 0.05 for many shares, but it differs by stock (RELIANCE is Rs 0.10; some ETFs and low-priced stocks use Rs 0.01). The tools use the tick saved in `trader/instruments.json` and assume Rs 0.05 only for a stock that is not saved there.
- Circuits: a share can hit a 2, 5, 10 or 20 % price band and stop trading for the day.
- Zerodha squares off open MIS positions near 15:25 (15:12 for some stocks) and charges
  Rs 50 + GST an order. The default flat-by is 15:10.
- Charges (equity intraday): brokerage 0.03 % or Rs 20 an order, whichever is lower; STT 0.025 % on the sell
  side; NSE transaction charge 0.00307 %; SEBI Rs 10 per crore; stamp duty 0.003 % on the buy; IPFT Rs 0.01 per crore;
  GST 18 % on brokerage, exchange and SEBI charges. Override any of it under `"costs"` in `trader/config.json`.

## Symbols

`RELIANCE`, `M&M`, `BAJAJ-AUTO`: the NSE trading symbol, upper case. Accepted and cleaned:
`reliance.ns`, `NSE:RELIANCE`, `BSE:500325` (BSE uses Yahoo's `.BO`). Yahoo gets `RELIANCE.NS`
(with `&` escaped in URLs); the Kite connector gets exchange `NSE` and tradingsymbol `RELIANCE`.

## Rules

- State the date a fact was read when you quote a holiday or a rate, and say they can change.
- Never invent a holiday: when the list does not cover a year, say so.

## Per-stock tick size and circuit limits
NSE tick sizes differ by stock (Kite reports Rs 0.10 for RELIANCE). The exit, budget and risk tools round stops, targets and limit prices to each stock's own tick, read from `trader/instruments.json`; a stock that is not in that file is treated as Rs 0.05 and `/trader-risk` says so as a warning.

Fill the file from the Kite connector: call its instrument search (`search_instruments`, query like `NSE:RELIANCE`), save the JSON result to a file, then:

```
python3 trader-market/market.py instrument add kite_search.json     # or - for stdin
python3 trader-market/market.py instrument show RELIANCE
```

It keeps NSE/BSE equity rows only (tick, lot size, token, ISIN, freeze quantity and the daily circuit limits). `/trader-risk` uses the saved circuit limits when `trader/prices.json` has none. Circuit limits change, so refresh them each morning.

## Kite data import

The other tools read candles and quotes from `trader/kite/`. Fill it like this:

1. Find the token once: Kite `search_instruments`, then `python3 market.py instrument add FILE`.
2. Fetch candles with Kite `get_historical_data` (interval `5minute`, `minute`, `15minute`, `60minute` or `day`), save the answer to a file, then `python3 market.py kite-import candles FILE --symbol RELIANCE --interval 5minute [--prev-close 1184.5]` (daily bars: `--interval day --out trader/kite/daily`).
3. Fetch quotes with Kite `get_ohlc` for the symbols you watch, save, then `python3 market.py kite-import quotes FILE` (the alert tool reads these; `ohlc.close` is the previous close).

Known limit: Kite's intraday history ends at the 15:15 bar, so the last 15 minutes of the session are missing; the import prints a note. Use the daily bar for the official close. Yahoo is the fallback when there is no Kite file.
