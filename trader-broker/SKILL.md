---
name: trader-broker
description: >-
  Order Executor for Zerodha Kite. Sends approved orders to your broker,
  practice mode by default, never without your yes. Shows each order as a
  plain ticket (the MIS limit entry, the stop-loss trigger, the target and
  the max loss at the stop) plus the exact arguments for the Kite connector's
  place_order tool, records your yes word for word, then Claude's own Allow
  once / Deny card asks again. After the entry fills it prepares the
  stop-loss and target orders for the shares you actually got (Kite has no
  bracket order), builds the sell ticket that closes a trade by your flat-by
  time before Zerodha's own auto square-off, records fills, and logs each
  trade's max loss so trader-risk can hold the daily limit. Covers connecting
  Kite, setting order tools to "Needs approval", and what Kite cannot do. Use
  when the user says "place the order", "buy it", "execute", "send it to
  Zerodha", "close my position", "flatten", "get me out", "connect Kite",
  or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

## Job and boundaries
- **Does:** shows READY tickets, records the user's word-for-word yes, prepares the exact `place_order` arguments, builds the stop-loss and target after the fill and the flatten ticket, and logs fills and each trade's max loss.
- **Does not:** place an order without all three gates, take one yes for two orders, act from a scheduled task, switch `paper` to live, ask for credentials, or touch futures, options, margin or shorts.
- **Needs first:** a trader-risk PASS from today for that exact order (matched by fingerprint) and the Kite connector logged in with order tools on 'Needs approval'.
- **Hands off to:** `/trader-alerts` for the stop, target and flat-by.
- **Stop and ask the user when:** every order (one question per order), a reply that is not an explicit yes, a failed order (new check and new yes), or an expired Kite session.


The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the order tool is set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

**Manual is the default.** The user places or approves every entry and exit. Automation exists, but only on a day the user turns it on in their own words (see "Automation" below).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ORDER_ID
python3 "${CLAUDE_SKILL_DIR}/ticket.py" exits --index 1 --qty 52          # after the entry fills
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side buy --qty 52 --price 1432.50
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 52
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
python3 "${CLAUDE_SKILL_DIR}/ticket.py" automate status                 # MANUAL or AUTOMATED
python3 "${CLAUDE_SKILL_DIR}/ticket.py" automate on --reply "automate today"   # only the user's own words
python3 "${CLAUDE_SKILL_DIR}/ticket.py" automate off
python3 "${CLAUDE_SKILL_DIR}/ticket.py" auto-approve --index 1          # replaces the typed yes, under the rules below
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the exit and flatten tickets, and keeps
the log in `trader/state/broker.json`. `--now "2026-10-26 10:15"` pins the clock
(India time). Read `kite_setup.md` in this folder for the one-time setup.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Setup: the Kite connector

Zerodha's Kite connector is an MCP server that Claude calls. The user adds it
(Customize > Connectors), logs in on Zerodha's own page (the login is the user's
action, never done in the chat, and expires every day), and sets permissions:

- `place_order`, `modify_order`, `cancel_order`, `place_gtt_order`,
  `modify_gtt_order`, `delete_gtt_order`: **Needs approval**. Never Always allow.
- Read-only tools (`get_orders`, `get_order_history`, `get_trades`,
  `get_positions`, `get_holdings`, `get_margins`, `get_quotes`, `get_ltp`,
  `get_ohlc`, `get_historical_data`, `search_instruments`): Always allow is fine.

In Claude Code, keep `mcp__kite__place_order` (and the other order tools) in the
`ask` list of `.claude/settings.json`, never in `allow`. The exact tool names depend
on how the connector is installed: use the names the tool list shows.

`"paper": true` in `trader/config.json` is **practice mode**. Zerodha has no
paper account, so practice mode means: the ticket is shown, the yes is recorded,
and Claude does NOT call `place_order`. The user sets `"paper": false` only
when they mean real money. Check `get_margins` and `get_profile` (read only) so
the user sees which account it is.

## What Kite does and does not do

- **No bracket orders.** An entry is a LIMIT order, product MIS, validity DAY.
  After it fills, two SELL orders protect the shares: a stop-loss (order type SL,
  trigger price and a limit a few ticks lower, so a fast fall still fills) and a
  target (LIMIT). `ticket.py exits` prints both for the shares that filled.
  They are NOT linked: when one completes, cancel the other with `cancel_order`.
  Between a fill and the stop being placed there is a gap of seconds: place the
  stop first.
- **SL-M** (stop-loss market) is not used by default; SL (stop-loss limit) is, with
  `stop_limit_buffer_ticks` (4) under the trigger. A gap through the limit leaves the
  order unfilled and the position open: watch it.
- **GTT** orders last longer than a day and are for delivery positions; they are not
  used for intraday.
- **MIS auto square-off.** Zerodha closes open MIS positions around 15:25 (about 15:12 for
  some stocks in the call-and-auction segment) and charges about Rs 50 plus GST per
  order. The flat-by default is 15:10 so the user closes first. Check zerodha.com for
  the current times.
- **MARKET orders** are restricted on illiquid stocks, so this pack uses limit orders
  and warns on market ones.
- **Circuits.** A stock at its upper or lower circuit may not fill buys or sells.
- **Sessions end daily.** If the connector says the session expired, ask the user to log
  in again. Never ask for a token or password in the chat.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. In practice mode, stop here and record `result --status denied --note practice`. Otherwise,
   only after "Recorded your yes", call the Kite connector's `place_order` with exactly the
   JSON the ticket printed. Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed --broker-id <order id>`,
   or `--status denied`, or `--status failed` with `--note`. A failed order is not retried
   without a new check and a new yes. Use `get_orders` to see the entry status.
6. When the entry is COMPLETE (`get_orders` / `get_order_history`), run
   `ticket.py exits --index N --qty <filled>` (and `ticket.py fill ...` to record the buy).
   The user's yes for the entry covers its stop-loss and target; the card still asks for each.
   Place the stop-loss first, then the target. If the entry only partly filled, cancel the
   rest and use the filled quantity.

## Steps: while it is open, and out by flat-by

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`).
- When the stop-loss or the target completes, cancel the other leg and record the sell:
  `ticket.py fill --symbol RELIANCE --side sell --qty 52 --price 1445.05`.
- By `flat_by` (15:10 IST), anything still open gets closed:
  1. Ask Kite what is held: `get_positions` (read only).
  2. `ticket.py flatten --symbol RELIANCE --qty 52` writes a market sell for those shares.
  3. `/trader-risk`. A sell that only closes what the user holds is never blocked by the
     daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. The stop-loss and the target still hold those shares, so Kite rejects a new sell until
     they are cancelled: `get_orders` shows them; `cancel_order` each one (Needs approval).
     Then `place_order` with the ticket, then `result`.

## Example (RELIANCE, practice mode)

_The prices in this example (RELIANCE near 1,432) are made up to show the arithmetic. They are not a quote and not a signal; real RELIANCE traded near 1,190 on 2026-10-23. Use live Kite prices._

```
ORDER 1 of 1, PRACTICE MODE (Claude will not place it), Zerodha Kite
  BUY 52 RELIANCE (NSE), limit 1432.50, MIS, good for today
  then stop-loss SELL, trigger 1424.10 (limit 1423.85)
  and target SELL, limit 1445.05
  max loss about ₹436.80 if the stop fills at 1424.10 (it can fill worse in a fast market, or not at all at a circuit)
  READY: trader-risk PASS, waiting for your yes
  Claude will call the Kite connector's place_order with exactly:
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
     "quantity": 52, "product": "MIS", "order_type": "LIMIT", "validity": "DAY", "price": 1432.5,
     "tag": "dtsa6d39e1bce04"}
```

The user replies "yes". Claude records it; if `"paper": false`, it calls `place_order` and the card
shows the arguments with Allow once and Deny. The `why` and the plan stay in the file; they are never sent.

## Automation (off unless the user turns it on, for one day at a time)

The user wants manual entries and exits until they have the confidence to hand them over. So:

- **Default: manual.** `ticket.py show` prints `MODE: MANUAL`. Every entry needs the user's typed yes (the gates above).
- **Turn on only on the user's own words**, e.g. "automate today" or "decide on your own today": `ticket.py automate on --reply "<their words>"` (add `--scope entry_exit|entry|exit`; default `entry_exit`). Never turn it on yourself, never from a vague reply ("sure", "go ahead"), a question, or a reply with a condition in it.
- **It lasts one trading day.** The next morning it is manual again; the user says it again to re-enable. `automate off` returns to manual at once; `automate status` shows the mode.
- **Practice first.** Automation in practice mode (`"paper": true`) counts a practice day each day it approves something. Live automation (`"paper": false`) is refused until `auto_practice_sessions` (default 5) practice days exist. The user still flips `paper` themselves.
- **What replaces the typed yes:** `ticket.py auto-approve --index N`. It refuses unless ALL of these hold: automation is on today; trader-risk passed that exact order today; for an entry, trader-research says CLEAR for that stock today (CAUTION only if `auto_allow_caution` is true; SKIP, missing or stale research means no); the entry is a LIMIT order with its stop-loss and target attached; the scope covers entry or exit. It never changes a limit.
- **Limits still bind:** budget, max loss per trade, daily loss, trades per day, entry window and flat-by are enforced by trader-risk as in manual mode. Exits, the stop-loss and target after a fill, and the flat-by close are never blocked by day limits.
- **Kite's own card:** while the connector's order tools are on "Needs approval", Claude's Allow once / Deny card still appears for each order. Fully unattended trading means the user chooses to change that setting themselves. Never change it or ask for credentials.
- **Never from a scheduled or background task** unless the user has turned automation on that day and the scheduled run is theirs; in practice mode nothing is sent either way.
- If any check fails, say which one and fall back to manual: show the ticket and ask for a yes.

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never places orders from a scheduled or background task unless the user turned automation on that day (a typed yes is impossible there).
- Never asks for, reads, stores or repeats passwords, tokens or API secrets.
- Never switches `"paper"` to live and never turns automation on. The user does that themselves.
- Never places futures, options, MTF, margin or short orders on its own.
- Never promises a fill price: a limit may not fill, and a stop fills at whatever the market gives.

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
