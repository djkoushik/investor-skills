---
name: trader-budget
description: >-
  Budget Manager for Indian intraday trades. Sizes every position to your
  budget and your max loss per trade, in rupees and whole shares. Reads the
  trade plan from trader-exit (entry, stop, target), finds the whole shares
  that fit inside four limits (max loss per trade, position cap, max order,
  cash after your buffer), says which limit decided it, shows the round-trip
  charges (brokerage, STT, exchange fees, GST, stamp duty) and the move
  needed to break even, and writes ONE proposed order (an MIS limit entry plus
  the stop-loss trigger and the target that go with it) to trader/orders.json
  in the shape of Zerodha Kite. A stop too far away for your max loss, or
  charges that eat too much of the risk, is a plain refusal, not a smaller
  order. Nothing is placed. Use when the user says "how many shares", "size
  this trade", "how much should I buy", "I have 1 lakh", "I only want to risk
  500 rupees a trade", "set my rules", "keep 10% in cash", or after
  /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

## Job and boundaries
- **Does:** sizes whole shares to four limits (max loss, position cap, max order, cash after buffer), says which one binds, shows charges and breakeven, and writes ONE proposed order to `trader/orders.json`.
- **Does not:** place orders, shrink a stop or loosen a limit to fit, size a short, or use MIS leverage.
- **Needs first:** today's `trader/plan.json` and `trader/config.json` (copy `config.example.json`; the user sets every number).
- **Hands off to:** `/trader-risk`.
- **Stop and ask the user when:** it refuses (zero shares, reward:risk under minimum, charges over `max_cost_r`, order under minimum) or the config does not exist yet.


The Budget Manager turns a plan ("buy RELIANCE at 1432.50, stop 1424.10") into
an exact order ("buy 52 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. trader-risk checks it and trader-broker asks the user yes or
no. Intraday trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1432.50 --json # a limit order at Rs 1432.50
python3 "${CLAUDE_SKILL_DIR}/size.py" --market               # a market order (not recommended)
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 250000        # override the budget in the config
```

`--now "2026-10-26 10:15"` pins the clock (India time). A plan for another day
is refused: make a new one with trader-exit. The entry is a LIMIT order at the
plan's entry by default.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for, in rupees. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default 500).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the budget),
  the biggest order (Rs 2,00,000) and the smallest order (Rs 500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (1,500), **max_trades_per_day** (3),
  **max_orders_per_day** (10), **min_reward_risk** (1.0), **max_cost_r** (0.5: the round-trip
  charges may not exceed half of what you risk), **require_stop** (true).
- The window: **no_entries_before** (09:30), **no_entries_after** (14:30), **flat_by** (15:10).
- **product** (MIS, intraday) and **stop_limit_buffer_ticks** (4: the stop-loss limit sits this many
  Rs 0.05 ticks under the trigger, so a fast fall still fills).
- **costs**: overrides for any charge rate (see below), left empty to use Zerodha's published rates.
- **paper** (true: practice mode, see trader-broker).
- **Automation** (read by trader-broker, all optional): **auto_practice_sessions** (5: practice days needed before live automation), **auto_scope** (`entry_exit`, `entry` or `exit`) and **auto_allow_caution** (false: a CAUTION research verdict is not automated). Automation itself is never a config value: the user switches it on, for one day, in the chat.

Write the file back and show the user the numbers. Defaults are examples,
not advice: the user sets their own risk.

## Step 2: size the trade

1. Run `size.py`. Read back the limit table: each limit's whole shares, and
   the one that binds.
2. The size is the smallest of the four. Sizes are whole shares only; the
   cash segment has no fractions. MIS leverage is not used: you size against
   your own cash. (Zerodha may offer more buying power; this pack does not rely
   on it.)
3. Prices are moved to the stock's own tick (Rs 0.05 unless `trader/instruments.json` says otherwise): the entry to the nearest tick,
   the stop down, the target up.
4. Read out the charges line: what the round trip costs at the target and at
   the stop, the breakeven move per share, and that as R. If it is over
   `max_cost_r` the tool refuses, because the trade has to win the charges
   back before it makes anything.
5. If it refuses (zero shares, reward:risk under the minimum, charges too high,
   order under the minimum), say the reason and the fix. Never shrink the
   stop or loosen a limit on your own.
6. With `--write`, it saves ONE order. Show it, then hand off to `/trader-risk`.

## Charges (Zerodha equity intraday, cash segment)

Brokerage 0.03 % or Rs 20 an executed order, whichever is lower; STT 0.025 %
on the sell side; exchange transaction charge 0.00307 % (NSE); SEBI Rs 10 per
crore; stamp duty 0.003 % on the buy side; investor protection fund Rs 0.01 per
crore; GST 18 % on brokerage, exchange and SEBI fees. These are the published
rates as of this writing, not guaranteed: confirm them at zerodha.com/charges,
and put any difference in `"costs"` in the config (keys: `brokerage_pct`,
`brokerage_max`, `stt_sell_pct`, `exchange_pct`, `sebi_per_crore`,
`stamp_buy_pct`, `ipft_per_crore`, `gst_pct`). A different broker needs its
own numbers. DP charges do not apply to intraday.

## The order it writes

A Kite-shaped proposal (strings): `symbol`, `exchange` NSE, `side` buy, `qty`,
`type` limit, `time_in_force` day, `product` MIS, `limit_price`, plus
`order_class: "bracket"`, `take_profit_limit_price`, `stop_loss_trigger_price`
and `stop_loss_limit_price`. Kite itself has no bracket order, so this is a
plan of three orders: the entry now, then the stop-loss (SL) and the target
(LIMIT) once the entry fills. trader-broker handles that. `why` and `plan` are
ours and are never sent.

## Example (RELIANCE, ₹1,00,000 budget, ₹500 max loss)

_The prices in this example (RELIANCE near 1,432) are made up to show the arithmetic. They are not a quote and not a signal; real RELIANCE traded near 1,190 on 2026-10-23. Use live Kite prices._

Plan: entry 1432.50, stop 1424.10 (risk 8.40 a share), target 1445.05.

```
Limits (whole shares; the smallest one wins):
  max loss      ₹500.00 max loss / 8.40 risk a share                 59 shares
  position cap  75% of ₹1,00,000.00 = ₹75,000.00 / 1432.50          52 shares  <- binds
  max order     ₹2,00,000.00 / 1432.50                              139 shares
  cash          budget minus the ₹10,000.00 buffer = ₹90,000.00 / 1432.50   62 shares

Charges (round trip, Zerodha rates): about ₹73.82 if the target fills, ₹73.50 if the stop does. That is 0.17R.
Size: 52 shares of RELIANCE = ₹74,490.00 at 1432.50. Max loss at the stop: 52 x 8.40 = ₹436.80 (before charges).
```

## Rules

- Whole shares, rupee amounts, tick-aligned prices. Say the binding limit.
- Never write an order without a stop. Never size a short.
- A refusal is a result, not an error: say it plainly.
- Show the charges every time. They are part of the trade.

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
