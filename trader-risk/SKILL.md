---
name: trader-risk
description: >-
  Risk Manager for Indian intraday trades. Checks every order against your
  limits and stops anything over them, before it can reach Zerodha. For an
  intraday trade: a stop is required, the loss at the stop must fit your max
  loss per trade (measured from the higher of the plan's entry and a fresh
  price), reward:risk must meet your minimum, the round-trip charges must not
  eat too much of the risk, the day's total risk must fit your daily loss
  limit, no more than your trades per day, and no new entries outside your
  entry window or when the NSE is closed. It also keeps the basics: blocked
  tickers, rupee order size, position cap, cash buffer, margin, short sales,
  futures and options, prices off the Rs 0.05 tick, a stop below the lower
  circuit, duplicates and a daily order limit. Getting out is never blocked by
  a day limit. Prints PASS or BLOCK with the reason and records the verdicts
  so trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my rules",
  "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

## Job and boundaries
- **Does:** checks every proposed order against the user's limits and writes PASS or BLOCK with the reason and a fingerprint of the exact order; an exit is never blocked by day limits.
- **Does not:** place or edit an order, loosen a limit, treat a PASS as the user's yes, or check F&O.
- **Needs first:** `trader/orders.json`, `trader/config.json`, a fresh price if the plan may have moved, and circuit limits in `prices.json` (without them the circuit check is silent and the tool warns).
- **Hands off to:** `/trader-broker`, only for orders that passed today.
- **Stop and ask the user when:** an order is BLOCKED (give the reason and the fix; never change the limit yourself) or the order changes after the check (re-run it).


The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker. Most people who trade intraday lose money. These
limits keep each loss and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-26 10:15"  # pin the clock (India time)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings; optional `detail[SYMBOL].upper_circuit / lower_circuit` |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates, charges) turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, no whole-share `qty` (a rupee `notional` is refused: Zerodha takes shares), a price that is not a multiple of Rs 0.05, a limit order with no limit price, an SL order with no trigger, an unknown type / validity / product / exchange, a bracket plan that misses its stop-loss or target |
| asset | futures or options symbols (like `NIFTY26OCTFUT`), or anything that is not an NSE ticker (letters, digits, `&`, `-`) |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the ticker is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy needs more than your cash (MIS leverage is not assumed; `allow_margin` allows it) or leaves less than the cash buffer |
| stop | (entries) no stop while `require_stop` is true; a stop on the wrong side of the entry |
| circuit | (entries) the stop is below the lower circuit (it could never trigger), or the entry is at the upper circuit (a buy may never fill). Needs the circuit limits in `prices.json` detail; without them it cannot check, and says nothing |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| cost | (entries) the round-trip charges at the target are more than `max_cost_r` (0.5) of the loss at the stop |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the market is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). An older price is not used,
  and it says so.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size trades use up a Rs 1,500 day.
- **Charges** use Zerodha's published rates (see trader-budget) or your
  `costs` overrides. They are not in the max loss; they sit on top.
- **The window** is India time (always +5:30, no daylight saving). Weekends and
  NSE holidays are closed. The 2026 holiday list is built in; add other years in
  `trader/holidays.json`, and the tool warns when the date is outside the list.
  Set both `no_entries_*` to null to switch the window off.
- There is no pattern-day-trader rule in India, so there is no `pdt_guard`.
  What does exist: SEBI's rules for derivatives, which this pack does not touch,
  and Zerodha's own MIS rules (auto square-off, margin).

Warnings do not block, but say them to the user: practice mode vs LIVE, a
MARKET order (no price control), product CNC (delivery is not squared off), Kite
having no bracket order (so the exits are separate), a stop that lives only in
the plan, a price too old to use, a holiday list that does not cover the date.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, the charges, and how much of today's limits it uses.
   For a BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a limit
   yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.

## Example (RELIANCE 52 shares from the trader-budget example)

_The prices in this example (RELIANCE near 1,432) are made up to show the arithmetic. They are not a quote and not a signal; real RELIANCE traded near 1,190 on 2026-10-23. Use live Kite prices._

At 9:55 AM on Monday 26 Oct 2026, fresh ₹1,00,000 account, example config:

```
PASS  #1 buy RELIANCE 52 shares, entry
          round-trip charges about ₹73.82 (breakeven +1.42 a share)
          entry 1432.50 (the limit price), stop 1424.10, target 1445.05: risk 8.40 a share
          max loss ₹436.80 (limit ₹500.00), reward:risk 1.49 (min 1)
          today before this: 0 of 3 trades, ₹0.00 of ₹1,500.00 daily loss
```

At 9:20 the same order is blocked by the window ("no new entries before 09:30
IST"). With 60 shares it is blocked twice:

```
        - position: RELIANCE would be ₹85,950.00, 86.0% of the ₹1,00,000.00 account, over your 75% cap
        - loss: 60 shares x 8.40 risk a share = ₹504.00 at the stop, over your ₹500.00 max loss per trade
```

A sell that closes the position at 15:10 passes even though it is after the
14:30 entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one that fills, and a
  stop can fill worse than its price in a fast market, or not at all at a circuit.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

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
