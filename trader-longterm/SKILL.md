---
name: trader-longterm
description: Long-term investing for Indian stocks (hold years). Fundamentals decide whether to own it; weekly/monthly technicals decide the price to buy at (200-day average, multi-year support zones, depth of the fall, weekly RSI) and give a staged-buying ladder. Ignores short-term news and market mood. Use for "long term", "should I invest in X", "where should I accumulate X", "is X a good buy for 5 years". Self-contained; needs the Tickertape connector for the fundamentals and Kite (or a CSV) for daily bars.
---

# Trader Long-Term (India)

## Job and boundaries
- **Does:** judges whether to own a business (fundamentals, cash flow, moat evidence, valuation models) and at what price to buy it (weekly/monthly zones, 200-day average, staged ladder); also runs portfolio weights, rebalancing and thesis checks.
- **Does not:** react to daily moves, news or market mood, place or size orders, or treat DCF output as a price target.
- **Needs first:** Tickertape fundamentals (stock tools only) and 2+ years of daily bars; moat scores need a written evidence sentence or they are ignored.
- **Hands off to:** the user's own buying plan; there is no order path from this skill.
- **Stop and ask the user when:** fundamentals data has gaps (verdict INCOMPLETE), a DCF input is your assumption, or a tax figure matters (it needs a CA).


**What matters here:** the business (fundamentals) first, then the price you pay (technical entry on long timeframes). **What does not:** daily moves, negative market mood, a bad results-day reaction, headlines. A great business bought at a long-term support base is how long-term money is made; the same business bought stretched is how it is lost.

This skill stands alone: its folder holds everything it runs (`longterm.py`, `ta.py`). It needs no other trader skill.

## Inputs
1. **Fundamentals** from Tickertape (Pro connector). Use only stock tools, never `pf_*`, `wl_*` or `us_*`:
   - `in_resolve_sid_read` (stock, NSE) -> SID.
   - `in_stock_overview_read` -> `price.last`; `highLow[0]` -> `high_52w`, `low_52w`.
   - `in_stock_analysis_read` sections `scorecard`: read the element `source.value` numbers: "YTD Return"/"5Y CAGR" -> `returns`; "TTM P/E ratio", "P/B ratio" -> `valuation`; "3Y rev. growth", "3Y EPS growth", "3Y CF growth" -> `growth`; "ROE", "Net profit margin", "Cash flow margin" -> `quality`. Red flags: read each element's `description`, not its `flag` (a flag of "High" there means no problem); set `asm`, `gsm`, `pledged`, `default_risk` true only when the description says the stock IS in the list / a lot is pledged / default risk is high.
   - `in_stock_financials_read` type `ratios`: `indpe` -> `industry_pe`, `indpb` -> `industry_pb`, `divYield`. Type `balance`, count 1: `balTdeb` -> `balance.debt`, `balTeq` -> `balance.equity`, `balCsti` -> `balance.cash`.
   - `in_stock_ownership_read` sections `holdings`: `pmPctT`, and the quarter-on-quarter change in percentage points for promoter, FII (`fiPctT`), DII (`diPctT`).
   - Optional `in_stock_extras_read` type `ratings` -> `analyst`.
   - `"financial_company": true` for banks and NBFCs. Leave out anything you cannot get; gaps are reported as "not checked".
2. **Daily bars** (2+ years; 5+ is better): Kite `get_historical_data` with interval `day` (Kite returns up to about 2000 days per call; chain calls for longer), or a CSV with date,open,high,low,close,volume. Tickertape history has closes only, so it cannot give the support zones.

## Run
```
python3 trader-longterm/longterm.py read facts.json bars.json      # the full read, saved to trader/longterm.json
python3 trader-longterm/longterm.py zones bars.json                # the price/entry view alone
python3 trader-longterm/longterm.py rules                          # every threshold
```
The facts shape is at the top of `longterm.py` (`FACTS_SHAPE`).

## Reading the result
| Verdict | Meaning |
| --- | --- |
| ACCUMULATE ZONE | Fundamentals CANDIDATE and price at a long-term support zone or a deep, washed-out pullback. Staged buying is reasonable to consider: the ladder lists the zones below, nearest first. |
| WATCHLIST | Price is attractive but fundamentals have a weak spot. Say which one and let the user decide. |
| WAIT | Price is stretched (far above its 200-day average or weekly RSI hot) or at no long-term support. Name the zones to set alerts at. |
| INCOMPLETE | Not enough fundamentals data. Fill the gaps first. |
| NO ENTRY | Fundamentals say AVOID. |

Buy in tranches at the ladder's zones, not all at once. The "re-check" level is where the thesis needs a fresh look, not an automatic sell. Re-check also on thesis-breaking news only (governance, rating downgrade, ASM/GSM, rising pledge, deteriorating business metrics).

## Limits
- Hindsight is easy: the zones come from past turning points and do not predict that a base will hold. A stock can break its base.
- Annual numbers, one snapshot. No forecasting or intrinsic-value model. Industry comparisons depend on how Tickertape classifies the company.
- It does not place orders and does not size a position; decide the amount from your own plan and diversification.

## Safety
Education and tooling, not financial advice. No credentials in chat.

## Added: cash flow, moat, valuation models, portfolio, thesis

**Two new pillars in `read`.** `Cash flow` uses `financials` (lists, oldest first, in crore): `revenue`, `net_profit`, `cfo`, and `capex` (or `fcf`). It rates how much operating cash flow backs the profit and in how many years free cash flow was positive. Tickertape fields: `incTrev` revenue, `incNinc` net profit, `cafCfoa` operating cash flow, `cafCexp` capex. `Moat` uses `moat` scores 0 to 2 for `network_effects`, `brand`, `switching_costs`, `cost_advantage`, `scale`, **plus** an `evidence` sentence. No evidence means the scores are ignored. The moat is a judgment: write what makes the business hard to copy. Industry notes come from `industry` {tailwinds, headwinds, cyclical}.

**`value facts.json`** runs a two-stage DCF with a growth and discount-rate sensitivity grid, a reverse DCF (what growth today's price already assumes), a dividend model, EV/EBITDA, PEG and the stock's own P/E percentile. Inputs sit in `valuation_inputs` (`fcf`, `shares` in crore, `growth_pct`, `discount_pct`, `terminal_pct`, optional `net_debt`, `dps`, `dividend_growth_pct`, `market_cap`, `ebitda`, `pe_history`). If you leave `growth_pct` out it borrows 3-year EPS growth (capped at 15%) and says so. The answer is a range from your assumptions, not a price target; the assumptions are yours to confirm.

**`portfolio holdings.json [--targets t.json] [--new-cash N] [--allow-sells]`** takes Kite `get_holdings` output (add `sector` and `buy_date` per holding for concentration and tax). It shows weights, flags a position above 15% or a sector above 35%, and plans a rebalance with new money first; sells happen only with `--allow-sells`. Tax estimate: short-term 20%, long-term 12.5% above Rs 1.25 lakh a year, with "wait N days" tips when a gain is within 45 days of turning long-term. The rates (as read in 2026; Indian tax law changes yearly) came from one secondary source and ignore surcharge, cess and loss carry-forward: confirm with a tax advisor.

**`thesis add SYMBOL --text "why" --break "roe_pct<12" --break "debt_to_equity>1"`** then **`thesis review SYMBOL facts.json`**. Price is deliberately not a break condition: a fall alone never breaks the thesis. Review exits with code 2 when a condition breaks.
