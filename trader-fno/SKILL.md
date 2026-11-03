---
name: trader-fno
description: F&O research for Indian index and stock options and futures: Greeks (delta, gamma, theta, vega), time-decay table, strategy payoff with max profit/loss and breakevens, option-chain read (PCR, max pain, OI walls), and a technical-levels view with headwinds (VIX, events, sector and market mood). Research only, places no orders. Use for "Nifty options", "what is the theta on this strike", "payoff of this spread", "is it a good time to buy a call". Self-contained.
---

# Trader F&O (India) - research only

## Job and boundaries
- **Does:** calculates and explains: Greeks, decay, IV vs HV, payoff, stress, chain read, buildup and underlying levels with headwinds.
- **Does not:** place or size orders, recommend a trade, compute SPAN margin, or state a lot size, expiry or margin from memory.
- **Needs first:** Kite for contract details, quotes and bars; Tickertape for market mood; the risk-free rate assumed is 6.5 % unless `--rate` is given.
- **Hands off to:** nothing; F&O orders are blocked on purpose across the pack.
- **Stop and ask the user when:** a structure has uncapped loss (say it first), a needed input would have to be guessed, or the user asks for an order.


**What matters here, all at once:** the underlying's technical levels (entry and exit), **short-term headwinds** (results, RBI/policy days, expiry, global cues), **time decay** (theta), **implied volatility and the Greeks**, and **sector and market sentiment**. Unlike the other horizons, a good view on direction is not enough: you can be right and still lose to decay or a volatility drop.

This skill only calculates and explains. It places no orders, and the rest of the toolkit blocks F&O orders on purpose. Options bought can lose their whole premium; options sold and futures can lose far more than the margin. The skill never nudges the user toward a trade.

Stands alone: `fno.py` and `ta.py` in this folder are all it needs.

## Getting the numbers (the user's Kite connector; this tool never invents them)
- Instrument details (lot size, expiry, strike, tick): Kite `search_instruments` (underlying `NFO:NIFTY`, `filter_on` underlying).
- Option premium, OI, volume: Kite `get_quotes` / `get_ltp` for the contract.
- Underlying daily bars: Kite `get_historical_data`, interval `day`.
- Market context: Tickertape `in_index_overview_read`, `in_market_overview_read` (sector and index mood). India VIX and the put/call ratio from the option chain.

## Run
```
python3 trader-fno/fno.py greeks --spot 24500 --strike 24600 --type call --dte 12 --iv 14      # or --price 140 to solve IV
python3 trader-fno/fno.py decay  --spot 24500 --strike 24600 --type call --dte 30 --iv 14
python3 trader-fno/fno.py payoff --legs "buy call 24600 140; sell call 24800 60" --lot 75 --spot 24500   # 75 and 24500 are placeholders: read the real lot size and spot from Kite
python3 trader-fno/fno.py chain  chain.json --spot 24500
python3 trader-fno/fno.py view   bars.json --facts facts.json      # support/resistance, typical move, VIX/events/mood
python3 trader-fno/fno.py rules
```
Shapes for `chain.json` and `facts.json` are at the top of `fno.py`.

## How to use the pieces (say these in plain words)
1. **View**: where are support and resistance, and how far does the underlying typically move in a day and in five (ATR)? A premium that needs a bigger move than that is expensive.
2. **Headwinds**: any event in the next 5 days? After it passes, option prices usually fall even if the underlying moves your way.
3. **Greeks**: delta (direction exposure), theta (the daily cost of holding), vega (sensitivity to volatility), gamma (how fast delta changes, dangerous near expiry).
4. **Decay table**: shows how much premium disappears if the underlying does not move; it speeds up inside the last ~10 days.
5. **Payoff**: for any structure, state the maximum loss and breakevens first. If the loss is not capped, say so plainly.
6. **Chain**: OI walls and max pain suggest where the crowd is positioned; they are tendencies and move as positions change.

## Limits
- Black-Scholes is a guide: it ignores skew, bid-ask spread, dividends and early-exercise details; real prices differ.
- Margin, brokerage, STT on exercise and slippage are not included.
- IV is a single number you supply or solve; the tool does not fetch a volatility surface.
- Never state a lot size, expiry or margin from memory: read them from Kite.

## Safety
Education and tooling, not advice or a recommendation. F&O is high risk and unsuitable for many people. No credentials in chat.

## Added: volatility, strategies, stress, depth, OI buildup

- `vol BARS --iv 14.2 [--iv-history vix.json] [--event-days N]`: HV20 and HV60, IV minus HV, **IV rank and percentile** (needs 60+ past readings; India VIX is a proxy for index IV), and a volatility-crush warning before events.
- `strategy NAME --spot --step --dte --iv --lot [--width] [--sell]` for straddle, strangle, bull-call, bear-put, iron-condor, iron-butterfly: legs priced by Black-Scholes (approximate; replace with live quotes), payoff summary, net delta/gamma/theta/vega. Calendar and ratio spreads are **not built**.
- `stress --legs "..." --lot --spot --dte --iv [--capital N --max-loss-pct 2]`: profit and loss for spot moves of -5% to +5% crossed with IV -5/0/+5, an overnight gap row, the expiry maximum loss, and a pass/fail against the loss cap (exit code 2 when over). **SPAN margin is not computed**; read it from Zerodha's margin calculator or the Kite margin tool.
- `depth QUOTE.json [--qty N]`: spread, buy/sell imbalance and impact cost from a Kite `get_quotes` depth block.
- `buildup CHAIN.json`: rows with `price_change` and `oi_change` labelled long buildup, short buildup, short covering or long unwinding.
