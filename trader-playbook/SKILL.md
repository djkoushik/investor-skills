---
name: trader-playbook
description: Start here. Decides which kind of trade the user wants (intraday, swing, long-term investing or F&O), then lists the skills to run in order and which are done, next or not built yet. Use when the user says "help me trade X", "should I invest in X", "what should I do today", "where do I start", or asks for a trade or investment without naming a step.
---

# Trader Playbook (India)

## Job and boundaries
- **Does:** asks the horizon once (intraday, swing, long-term, F&O), prints the ordered steps with done / NEXT / todo / NOT BUILT, and names the one skill to run next.
- **Does not:** trade, size, analyse or place anything, and never invents a step for a path the tools do not cover.
- **Needs first:** the user's horizon; the `trader/` folder is read only to mark steps done.
- **Hands off to:** the horizon's own skill (see the table below).
- **Stop and ask the user when:** the horizon is not clear from the request.


The router for the other skills. It never trades. It picks the path, shows what is finished, and says honestly what does not exist yet.

## 1. Ask the horizon (one question, if it is not clear)
- **Intraday**: in and out the same day (Zerodha MIS), cash segment.
- **Swing**: days to weeks.
- **Long-term**: months to years.
- **F&O**: futures and options.

If the user asks about "X" with no horizon, ask once. Do not guess for them.

## 2. Show the steps
```
python3 trader-playbook/playbook.py steps intraday --symbol RELIANCE
python3 trader-playbook/playbook.py horizons
```
Steps are marked `done`, `NEXT`, `todo` or `NOT BUILT`. Run the NEXT step with its skill, then re-run the playbook.

## 3. What each horizon is, and what it weighs
| Horizon | Skill | Decides on | Matters little |
| --- | --- | --- | --- |
| Intraday | the intraday pipeline (scanner, research, news, candles, entry, exit, timemachine, budget, risk, broker, alerts) | opening range, volume, costs, strict risk limits | fundamentals |
| Swing | `/trader-swing` | technicals: trend, support/resistance, pivots; events and weak markets only shrink the size | fundamentals (a note, except red flags) |
| Long-term | `/trader-longterm` | fundamentals first, then a weekly/monthly technical entry zone | daily moves, market mood, news |
| F&O | `/trader-fno` | technicals + time decay + Greeks + volatility + events + sector mood | nothing is ignored; research only, no orders |

Each horizon skill is self-contained: a person who only wants long-term investing installs `trader-longterm` alone. Only intraday orders can be placed through `/trader-broker`; swing entries and stops are placed by hand in Kite, and F&O orders are blocked on purpose. Never improvise a stop, size or order for a path the tools do not cover.

## 4. Rules for the whole flow
- Practice mode by default; `/trader-broker` places nothing without the user's yes at each gate. Entries and exits are manual unless the user says to automate ("automate today"); that lasts one day, needs a CLEAR research verdict and a risk PASS per order, and live automation needs practice days first. Never suggest turning it on.
- `/trader-research` and `/trader-candles` are advisory: they never change a size, limit or order.
- Set up once per stock: save its Kite instrument with `market.py instrument add` so the tick size and circuit limits are right.
- Education and tooling, not financial advice. No credentials in chat.
