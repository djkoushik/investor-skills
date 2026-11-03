---
name: trader-timemachine
description: >-
  Time Machine for NSE intraday plans. Replays every possible outcome of a
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit (entry, stop, target),
  rebuilds the rest of the day 10,000 times from the stock's own 5-minute
  bars over the last 60 days, and re-runs the same rule on every past
  session. Prints the odds of target first, stop first, or neither by your
  flat-by time, where the price sits then, the average R, the history, the
  round-trip charges (brokerage, STT, GST and the rest) and one honest
  verdict, then hands off to /trader-budget. Use when the user says "run the
  time machine", "replay this trade", "what are the odds", "simulate this
  trade", "monte carlo", "backtest this setup", "how often does this hit the
  target", "is this a coin flip", or after /trader-exit writes a plan, before
  /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---
> **Data source: Kite.** Candles come from the Kite connector via `market.py kite-import` into `trader/kite/` (see trader-market); this tool reads that folder when `--offline` is not given. With no Kite file it falls back to Yahoo (`INDIA_ALLOW_YAHOO=0` turns that off). Kite's intraday history stops at 15:15.


# trader-timemachine

## Job and boundaries
- **Does:** replays the plan 10,000 times from the stock's own recent 5-minute bars plus every past firing of the same rule, and reports odds, average R before and after charges, the seed and one verdict.
- **Does not:** predict, size, change the plan, or tune it until it looks good; a changed plan is re-run and the change is said.
- **Needs first:** today's `trader/plan.json` and at least 20 usable 5-minute sessions (every bar from 9:15 through the last bar before the flat-by).
- **Hands off to:** `/trader-budget` if the user still wants the trade.
- **Stop and ask the user when:** it refuses for too few usable sessions; see 'Kite data and Yahoo gap-filling' below. Say so plainly and do not substitute a guess.


The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session, and then shows what the round trip costs in
charges, because on the NSE a small target can be eaten by costs. It is a
simulation built from the stock's own recent bars, not a prediction.
Intraday trading loses most people money, and a good-looking replay does not
change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It imports `../trader-market/market.py`, so keep the folders side by side.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays + history
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # -> trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --seed 7 --replays 20000
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --notional 50000   # rupees per trade, for the charges line
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --entry 1483.15 --stop 1475.60 --target 1490.70 --entry-time "9:35 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart                  # ASCII fan chart of the replays
```

Other flags: `--until HH:MM` (default: the plan's flat-by), `--window N`
(draw from the same time of day plus or minus N bars, default 2),
`--offline FILE` (a saved Yahoo chart JSON), `--now "2026-10-26 10:15"` (India
time), `--json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit |
| `trader/config.json` | `flat_by`, `max_order_inr` (the default trade size for the charges line), optional `costs` overrides |
| `trader/kite/` (Kite imports) | the stock's 5-minute bars, read first when the folder exists |
| Yahoo chart API | the last 60 days of 5-minute bars (`.NS`), free and unofficial: the main feed when there is no Kite file, otherwise only a one-bar gap filler |
| `trader/timemachine.json` | with `--write`: the odds, the history, the verdict, the seed |

## How it works

1. **The pool.** It keeps only complete sessions before the plan's date: every
   five-minute bar from 9:15 IST through the last bar before the flat-by (all 75 if
   the flat-by is later; see the Kite section below). Holidays, short special sessions (like
   Muhurat trading), gaps and today are left out. Every bar becomes four
   ratios to the bar before it: open, high, low and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default your flat-by,
   15:10). Green, which means target first or above the entry at `--until`.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at `--until` divided by the risk. A gap or a circuit lock through
   the stop still counts as -1R here. A real fill can be worse.
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R) runs again on every one of those past sessions. That is
   what really happened, not a simulation.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them.
6. **The charges line.** Using Zerodha's published rates (brokerage 0.03 % or
   Rs 20 an order, whichever is lower; STT 0.025 % on the sell side; exchange
   fees; SEBI fee; stamp duty on the buy; GST), it shows what a round trip
   costs on `--notional` rupees at the target and at the stop, the move needed
   just to break even, and that as a fraction of R. Then it restates the
   average R after costs. A replay can look fine and be a loser once charges
   come off. Rates change: confirm them at zerodha.com/charges, and put any
   difference in `costs` in `trader/config.json`.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-minute bars but only about 7
days of 1-minute bars. The tool refuses, in one plain line, when there are
fewer than 20 usable sessions, when the entry bar is not in the data, or when the
plan does not make sense (a stop above a buy's entry, an entry off the
5-minute grid). A live in-progress bar is never used. Yahoo's India intraday
data can lag or have gaps: if the run complains, say so.

## Kite data and Yahoo gap-filling

Kite's intraday history ends at the 15:15 close (72 five-minute bars, checked 2026-10-24). The replay never needs the last three bars, because it stops at the flat-by (15:10). So a past session counts when it has **every bar from 9:15 through the last bar before the flat-by**, with no hole; it does not need all 75 bars. With the default flat-by of 15:10 that is 71 bars, and Kite sessions qualify.

- Kite is the main feed (months of history can be imported in chunks). Yahoo is asked for bars only when a Kite session has a hole before the flat-by, and it fills exactly that bar, never overwriting a Kite bar and never averaging two feeds. Turn this off with `INDIA_ALLOW_YAHOO=0`.
- The report says "Data: one feed, nothing patched." or how many bars in how many sessions came from Yahoo. Say that line to the user.
- If the user sets `--until` later than the last bar in the data (for example 15:30 with Kite data), the tool refuses and says each session needs every bar through the 15:25 bar. Do not pad bars by hand.
- When every session has all 75 bars the numbers are identical to the old rule (same seed, same result).
- A plan from `/trader-exit` always carries a flat-by. With no flat-by in the plan and no `--until`, the replay runs to 15:30 and then needs all 75 bars.
- Tested offline with synthetic data (tests/test_timemachine_gaps.py); not yet run against real Kite files.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at flat-by, the average R, the history line, the
   charges line, then the verdict line and any line under it exactly as
   printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too. If
   the average R after charges is negative, say that first.
5. Say once that this is built from the last 60 days and is not a prediction.
   Earnings, results announcements, news and halts can do things those 60
   days never did. Check `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example (the repo's offline test fixture: synthetic bars, not real prices)

RELIANCE, Friday 23 Oct 2026, the plan from the `trader-exit` example, seed 23:

```
RELIANCE buy, Friday 2026-10-23: entry 1483.15 at 9:35 AM (bar 3), stop 1475.60, target 1490.70 (1R, risk 7.55 a share).
10,000 replays of the rest of the session, 9:35 AM to 3:10 PM (67 five-minute bars), seed 23.

  target first   50.3 %
  stop first     40.4 %
  neither        9.2 %    still open at 3:10 PM
  green          55.1 %   target first, or above the entry at 3:10 PM

History: ... on each of those 40 real sessions fired 33 times: 17 target, 13 stop, 3 neither. Average R +0.107.
Leans toward the target: target first 50.3 % vs stop first 40.4 %.
Costs: on ₹50,000 (33 shares) a round trip costs about ₹52.18 at the target ... 0.209 R. The replays above are gross: average R after costs is about -0.110.
```

The point of the example: a plan that leans toward the target before costs
can still be a loser after them.

## Rules

- A simulation, not a prediction. Say so once, plainly.
- Always show the seed and the charges line.
- Never show a result as a promise of profit.
- Do not tune the plan until the replay looks good: that is how people fool
  themselves. If the user changes the plan, run it again and say what changed.

## What it never does

It never places an order, never sizes a position, and never changes the
plan.

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
