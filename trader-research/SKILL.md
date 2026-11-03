---
name: trader-research
description: Pre-trade research check for Indian stocks using the Tickertape connector. For each watchlist stock it looks for ASM/GSM listing, pledged promoter shares, results or board meetings, trading halts, negative rating actions, big bulk/block sells and FII selling, then records a CLEAR / CAUTION / SKIP verdict. Advisory only. Use after /trader-scanner and before /trader-news, or when the user says "research RELIANCE", "is it safe to trade X today", "any red flags on X".
---

# Trader Research (India)

## Job and boundaries
- **Does:** answers one question per stock: is there a reason not to day-trade it today (ASM/GSM, pledge, default risk, results or board meeting, halt, rating cut, big sell deal, FII/promoter selling)? Records CLEAR / CAUTION / SKIP in `trader/research.json`.
- **Does not:** change a size, limit or order, give a buy view, turn Tickertape scores or analyst targets into a SKIP, or invent a missing fact (it is listed as 'not checked').
- **Needs first:** the Tickertape connector and the stock's SID from `in_resolve_sid_read`.
- **Hands off to:** `/trader-news`, then `/trader-entry`. For a SKIP, tell the user to drop the stock from today's plan.
- **Stop and ask the user when:** Tickertape is not connected, an SID cannot be resolved, or a corporate-action announcement cannot be classified.


Advisory in manual mode: it never changes a size, a limit or an order. In automated mode (trader-broker, only on a day the user switched it on) a CLEAR verdict recorded today is required before an entry is auto-approved; CAUTION, SKIP, missing or stale research means the order goes back to the user. It answers one question: **is there a reason not to day-trade this stock today?** Tickertape's scores and analyst targets describe months, not minutes, so they are shown as notes and never turn a stock into a SKIP.

## Needs
The Tickertape connector (Pro). If it is not connected, say so and stop. Use only the market and stock tools. Never use the `pf_*`, `wl_*` or `us_*` tools.

## Steps (per stock, run stocks in parallel)
1. `in_resolve_sid_read` (assetType stock, exchange NSE) to get the SID. Never guess an SID.
2. `in_stock_overview_read` -> `price.last` and `price.change_pct` (the quote's `change` as a percent).
3. `in_stock_analysis_read` with sections `scorecard` and `checklists`:
   - scorecard items Performance, Valuation, Growth, Profitability -> `scorecard` (0-10), "Entry point" -> `entry_point`.
   - the "Red flags" item -> `red_flags`: ASM -> `asm`, GSM -> `gsm`, Promoter pledged holding -> `pledged`, Unsolicited messages -> `unsolicited`, Default probability -> `default_risk`. Read each element's `description`, not its `flag` (a flag of "High" there means no problem). Set a flag to `true` only when the description says the stock IS in the list, a lot is pledged, or default risk is high.
4. `in_stock_corporate_read` (announcements, upcoming and past ~5 days) -> `events`. Map each item: results or earnings -> `results`; board meeting intimation -> `board_meeting`; credit rating -> `rating` (direction `neg` for a downgrade or negative outlook); trading suspension/halt -> `halt`; dividend, bonus, split -> `dividend` / `bonus_split`; otherwise skip it.
5. `in_stock_ownership_read` sections `holdings` -> `ownership` (latest quarter minus previous, in percentage points: `promoter_change`, `fii_change`, `dii_change`).
6. `in_deals_read` (bulk and block deals, last 5 days) -> `bulk_deals` with `value_cr` in crore. If the call fails, leave the key out; it will show as "not checked".
7. Optional: `in_stock_extras_read` (ratings) / `in_stock_financials_read` (estimates) -> `analyst` {count, buy_pct, upside_pct}.

Leave out anything you could not get. Never invent a value: missing facts are listed as "not checked".

## Record
Write the facts as JSON (the shape is in the docstring at the top of `research.py`), then:

```
python3 trader-research/research.py record facts.json     # one stock or {"stocks": [...]}
python3 trader-research/research.py show
python3 trader-research/research.py check RELIANCE        # exit 0 CLEAR, 1 CAUTION, 2 SKIP, 3 none today
python3 trader-research/research.py rules                 # the thresholds
```

Verdicts are saved to `trader/research.json` and are valid for the day they were recorded.

## Reading the result
- **SKIP**: ASM/GSM listing, high default risk, results/board meeting/halt today. Tell the user plainly and suggest leaving it out of today's plan.
- **CAUTION**: pledged shares, results within 3 days, a move near the price band, a big recent sell deal, falling FII/promoter holding. Mention it; the user decides.
- **CLEAR**: nothing found. Say what was and was not checked. It is not a buy signal.

Report each stock in a line or two with the reasons. Do not recite the notes unless asked.

## Known limits
- Tickertape's `in_market_news_read` returned errors in testing; use corporate announcements and `/trader-news` for news.
- Corporate-action classification is your judgment from the announcement text; when unsure, leave it out and say so.
- Thresholds are in `RULES` at the top of `research.py`; change them there.

## Safety
Practice mode by default. Education and tooling, not financial advice. No F&O. Never ask for or paste credentials in chat.
