---
name: trader-exit
description: >-
  Exit Planner for NSE intraday trades. Sets your stop and your target before
  you enter, so you know when to get out. Reads the entry from trader-entry
  (or a price you give), puts the stop at the opening-range midpoint by
  default (or the range low, your price, a percent, or a multiple of the
  ATR), moves it DOWN to the Rs 0.05 tick, and sets the target at 2R (or 1R,
  or your price) rounded UP to the tick. Refuses a stop at or above the entry,
  a target at or below it, or a stop too tight to survive the spread, and
  says why. Writes trader/plan.json for the Time Machine, the Budget Manager
  and Price Alerts. Long side only. Use when the user says "where's my stop",
  "set my stop and target", "plan the exit", "when do I get out", "stop
  loss", "what's my R", "risk reward", or right after /trader-entry triggers,
  before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---
> **Data source: Kite.** Candles come from the Kite connector via `market.py kite-import` into `trader/kite/` (see trader-market); this tool reads that folder when `--offline` is not given. With no Kite file it falls back to Yahoo (`INDIA_ALLOW_YAHOO=0` turns that off). Kite's intraday history stops at 15:15.


# trader-exit

## Job and boundaries
- **Does:** sets the stop and target before entry on the stock's own tick (stop rounded down, target up), computes risk a share and reward:risk, sets flat-by, and writes `trader/plan.json`.
- **Does not:** size, place orders, move a stop away from the entry, widen a stop to make a plan pass, or plan a short.
- **Needs first:** a triggered `trader/entry.json` (or `--entry` to plan ahead); a level setup needs a stop the user names.
- **Hands off to:** `/trader-timemachine`, then `/trader-budget`; offer `/trader-alerts`.
- **Stop and ask the user when:** the plan is refused (stop at or above entry, target at or below it, risk under two ticks), or a level setup has no stop.


The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap, a fast market or a circuit lock can fill past it or not at all.
Intraday trading loses most people money, and a neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1483.15 --or-high 1478 --or-low 1473.20
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:10`, `--json`, `--now "2026-10-26 10:15"` (India
time), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup
level`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:10) and `min_reward_risk` (1.0: trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding to the tick.** Each stock has its own tick (Rs 0.05 unless
  `trader/instruments.json` says otherwise, e.g. Rs 0.10 for RELIANCE; the tool
  reads it). The stop is
  rounded DOWN to a tick (away from the entry for a long), the target UP.
  Risk = entry minus stop. Target = entry + R x risk. `2R` by default; `1R`,
  `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 2 ticks (Rs 0.10) or under 0.05% of the price: too
  tight to survive the spread. An entry.json that has not triggered (unless
  you give `--entry`). A short: the plans here are long only.
- **Flat by.** 15:10 IST by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. Zerodha squares off MIS positions itself
  around 15:25 (15:12 for some stocks) and charges a fee per order, so the
  default leaves room; the tool warns if you set a later time.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time.

## Example (the repo's offline test fixture: synthetic bars, not real prices)

RELIANCE, Friday 23 Oct 2026. The entry is 1,483.15 at 9:35 AM and the opening
range is 1,473.20 to 1,478.00. `exit.py plan --stop or-mid --target 1R --write`:

```
Exit plan: RELIANCE long, Fri 2026-10-23, opening-range breakout on 5-minute bars
  Entry         ₹1,483.15 at 9:35 AM (bar 3)
  Stop          ₹1,475.60  opening-range midpoint: (₹1,478.00 + ₹1,473.20) / 2 = ₹1475.6
  Target        ₹1,490.70  1R (one times the risk)
  Risk a share  ₹7.55 (0.51% of the entry)
  Reward:risk   1.0
  Flat by       15:10 IST, win or lose: no overnight hold
```

The default 2R target would be ₹1,498.25.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money.
- Long side only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

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
