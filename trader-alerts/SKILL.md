---
name: trader-alerts
description: >-
  Price Alerts for the NSE. Rings when a price, a stop or the opening bell
  hits. Turns the trade plan into three alerts in one go (the stop, the target
  and "flat by" at your flat-by time), plus above / below / stop / target
  levels, percent moves from the previous close, any time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours
  (9:15 to 15:30 IST) and the exchange holiday list, works out India time
  itself, reads quotes from Kite files (Yahoo chart JSON as fallback) one
  ticker at a time, and writes trader/prices.json for sizing and the risk
  check. Use when the user says "alert me when", "tell me if RELIANCE hits",
  "watch my stop", "watch my trade", "remind me to close by 3:10", "is the
  market open", "when does the market open", "get me a price", or right after
  /trader-exit writes a plan.
argument-hint: "[ticker] [price]"
---

# trader-alerts

## Job and boundaries
- **Does:** rings once on a price, stop, target, percent move, time of day or the opening bell; keeps the NSE clock; writes the latest quotes to `trader/prices.json`.
- **Does not:** place, cancel or change an order, present a delayed quote as live, or predict a price.
- **Needs first:** Kite quotes imported to `trader/kite/` (Yahoo is the fallback), and a plan for `--from-plan`.
- **Hands off to:** `/trader-broker` (`exits`, `flatten`) when a stop, target or flat-by rings, and only with the user's yes.
- **Stop and ask the user when:** a stop or target rings (check `get_orders` and `get_positions` first), or the holiday list does not cover the year.


Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. On Zerodha the stop-loss and the target are
orders the user places after the entry fills (trader-broker `exits`); the
alerts tell the user what happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1424.10
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1445.05
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-26 15:10"` pins the clock (India time); `--offline DIR`
reads saved quote files instead of the network. Tickers are NSE symbols
(`RELIANCE`, `M&M`, `BAJAJ-AUTO`); `.NS` and `NSE:` prefixes are accepted.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the tickers held, for `quote --holdings` |
| `trader/holidays.json` | optional: extra NSE holidays or special sessions, and the years they cover |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the India clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that India date only; `--from-plan`
rules carry the plan's date, so yesterday's stop never rings today.

## Quotes

- Source is **Kite**. Call Kite `get_ohlc` for the symbols (instruments like `NSE:RELIANCE`), save the answer, run
  `python3 ../trader-market/market.py kite-import quotes FILE`, then `alerts.py check` reads `trader/kite/` on its own
  (with no Kite file for a symbol it falls back to Yahoo; `--source yahoo` forces Yahoo). Each price in `trader/prices.json` keeps its
  quote time, and trader-risk only uses one under 15 minutes old. Kite's `ohlc.close` is the previous close, so percent
  moves are measured from it.
- You can also write the price straight into `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE": 1190}}`.
  Kite quotes also give the upper and lower circuit: put them in
  `"detail": {"RELIANCE": {"upper_circuit": 1575.7, "lower_circuit": 1289.3}}` and trader-risk will use them.

## The NSE clock

Regular session 9:15 to 15:30 IST, Monday to Friday, closed on exchange
holidays. The built-in list covers 2026 (from the NSE circular, with
Muhurat trading on Sunday 8 Nov noted). For any other year the tool says the
list is unknown: add the dates in `trader/holidays.json` from nseindia.com
(`{"years": [2027], "holidays": {"2027-01-26": "Republic Day"}}`). Special
sessions (like Muhurat trading, whose hour is announced yearly) go under
`"special_open"`. India has no daylight saving, so the offset is always +5:30.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means an
   exit order has probably filled: check `get_orders` and `get_positions` with
   the Kite connector, cancel the other exit leg, and record the fill with
   trader-broker. "Flat by" means close anything still open before Zerodha's
   own auto square-off: trader-broker `flatten`, then trader-risk, then the
   user's yes. Never act for them.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.
- Remember circuits: a stock locked at its upper or lower circuit may not fill
  anything, so a stop alert is not a guarantee of an exit.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

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
