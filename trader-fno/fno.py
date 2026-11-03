#!/usr/bin/env python3
"""F&O research tools for trader-fno. Standard library only, no network. RESEARCH ONLY: places no orders.

An options trade is decided by FOUR things at once: where the underlying is (technical levels), how
much time is left (decay), how expensive options are (implied volatility, the Greeks) and what is
about to happen (events, sector and market mood). This tool puts numbers on each.

    python3 fno.py greeks --spot 24500 --strike 24600 --type call --dte 12 --iv 14 [--price 140]
    python3 fno.py decay  --spot 24500 --strike 24600 --type call --dte 30 --iv 14
    python3 fno.py payoff --legs "buy call 24600 140; sell call 24800 60" --lot 75 --spot 24500
    python3 fno.py chain  chain.json --spot 24500                 # PCR, max pain, OI walls
    python3 fno.py view   bars.json [--facts facts.json]          # technical levels + headwinds
    python3 fno.py rules

Greeks use Black-Scholes (European options, which index options are; stock options on NSE are also
European and physically settled). --iv is in percent a year. --rate is the risk-free rate in percent
(default 6.5). Premiums, lot sizes and expiry dates come from Kite, never from this tool. The model is a
guide: real prices differ by spreads, skew and dividends. Education and tooling, not advice.

chain.json: a list of {"strike": 24600, "type": "call"|"put", "oi": 1200000, "ltp": 140, "iv": 14}
facts.json (for view): {"symbol": "NIFTY", "india_vix": 14.2, "pcr": 0.9, "sector_trend": "up|down|sideways",
  "sentiment": "positive|negative|neutral",
  "events": [{"kind": "RBI policy", "date": "2026-12-05"}, {"kind": "expiry", "date": "2026-10-29"}]}
"""
import argparse
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ta  # noqa: E402

RULES = {
    "vix_high": 20.0, "vix_low": 13.0,       # rules of thumb for India VIX, not exchange definitions
    "pcr_bullish": 1.2, "pcr_bearish": 0.7,
    "event_days": 5,
    "theta_fast_dte": 10,                     # decay accelerates inside about this many days
}
SQRT2 = math.sqrt(2.0)


def cdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs(spot, strike, dte, iv_pct, kind, rate_pct=6.5):
    """-> dict(price, delta, gamma, theta_day, vega_pt, rho_pt). dte in calendar days."""
    if spot <= 0 or strike <= 0 or iv_pct <= 0:
        raise ta.TaError("spot, strike and iv must be positive")
    T = max(dte, 1e-6) / 365.0
    s, r = iv_pct / 100.0, rate_pct / 100.0
    sq = s * math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * s * s) * T) / sq
    d2 = d1 - sq
    disc = math.exp(-r * T)
    if kind == "call":
        price = spot * cdf(d1) - strike * disc * cdf(d2)
        delta = cdf(d1)
        theta = -(spot * pdf(d1) * s) / (2 * math.sqrt(T)) - r * strike * disc * cdf(d2)
        rho = strike * T * disc * cdf(d2)
    elif kind == "put":
        price = strike * disc * cdf(-d2) - spot * cdf(-d1)
        delta = cdf(d1) - 1.0
        theta = -(spot * pdf(d1) * s) / (2 * math.sqrt(T)) + r * strike * disc * cdf(-d2)
        rho = -strike * T * disc * cdf(-d2)
    else:
        raise ta.TaError("type must be call or put")
    return {"price": price, "delta": delta, "gamma": pdf(d1) / (spot * sq), "theta_day": theta / 365.0,
            "vega_pt": spot * pdf(d1) * math.sqrt(T) / 100.0, "rho_pt": rho / 100.0}


def implied_vol(price, spot, strike, dte, kind, rate_pct=6.5):
    """Bisection on volatility (percent). Raises if the price is outside what the model allows."""
    lo, hi = 0.5, 400.0
    if not bs(spot, strike, dte, lo, kind, rate_pct)["price"] <= price <= bs(spot, strike, dte, hi, kind, rate_pct)["price"]:
        raise ta.TaError("that premium cannot be matched by the model (below intrinsic value or too high)")
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if bs(spot, strike, dte, mid, kind, rate_pct)["price"] < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def parse_legs(text):
    """"buy call 24600 140; sell call 24800 60 [2]" -> [(side, kind, strike, premium, lots)]"""
    legs = []
    for part in text.split(";"):
        w = part.split()
        if not w:
            continue
        if len(w) not in (4, 5) or w[0].lower() not in ("buy", "sell") or w[1].lower() not in ("call", "put"):
            raise ta.TaError('leg %r: write "buy|sell call|put STRIKE PREMIUM [LOTS]"' % part.strip())
        legs.append((w[0].lower(), w[1].lower(), float(w[2]), float(w[3]), int(w[4]) if len(w) == 5 else 1))
    if not legs:
        raise ta.TaError("give at least one leg")
    return legs


def payoff_at(legs, lot, price):
    total = 0.0
    for side, kind, k, prem, lots in legs:
        intrinsic = max(price - k, 0.0) if kind == "call" else max(k - price, 0.0)
        pnl = (intrinsic - prem) if side == "buy" else (prem - intrinsic)
        total += pnl * lot * lots
    return total


def payoff_summary(legs, lot, spot=None):
    """Payoff at expiry is piecewise linear, so its extremes and zero-crossings are exact at the strikes."""
    ks = sorted(set(k for _, _, k, _, _ in legs))
    top = max(ks) * 2
    pts = sorted(set([0.0, top] + ks))
    vals = [payoff_at(legs, lot, x) for x in pts]
    slope_up = (payoff_at(legs, lot, top * 2) - payoff_at(legs, lot, top)) / top
    unlimited_loss = slope_up < -1e-9
    unlimited_profit = slope_up > 1e-9
    be = []
    for i in range(1, len(pts)):
        y0, y1 = vals[i - 1], vals[i]
        if y0 == 0 and i == 1:
            be.append(round(pts[i - 1], 2))
        if y0 * y1 < 0:
            be.append(round(pts[i - 1] + (pts[i] - pts[i - 1]) * (0 - y0) / (y1 - y0), 2))
        elif y1 == 0 and i > 0 and y0 != 0:
            be.append(round(pts[i], 2))
    if unlimited_profit or unlimited_loss:
        y0, y1 = vals[-1], payoff_at(legs, lot, top * 2)
        if y0 * y1 < 0:
            be.append(round(top + top * (0 - y0) / (y1 - y0), 2))
    return {"max_profit": None if unlimited_profit else round(max(vals), 2),
            "max_loss": None if unlimited_loss else round(min(vals), 2),
            "breakevens": sorted(set(be))[:6], "unlimited_profit": unlimited_profit, "unlimited_loss": unlimited_loss,
            "net_premium_paid": round(sum((p if s == "buy" else -p) * lot * n for s, _, _, p, n in legs), 2)}


def chain_read(rows, spot=None):
    calls = {r["strike"]: r for r in rows if r["type"] == "call"}
    puts = {r["strike"]: r for r in rows if r["type"] == "put"}
    coi, poi = sum(r.get("oi") or 0 for r in calls.values()), sum(r.get("oi") or 0 for r in puts.values())
    strikes = sorted(set(calls) | set(puts))
    pain = {}
    for x in strikes:
        pain[x] = sum((calls[k].get("oi") or 0) * max(x - k, 0) for k in calls) + sum((puts[k].get("oi") or 0) * max(k - x, 0) for k in puts)
    top_c = sorted(calls.values(), key=lambda r: -(r.get("oi") or 0))[:3]
    top_p = sorted(puts.values(), key=lambda r: -(r.get("oi") or 0))[:3]
    return {"pcr": round(poi / coi, 2) if coi else None, "max_pain": min(pain, key=pain.get) if pain else None,
            "call_walls": [(r["strike"], r.get("oi")) for r in top_c], "put_walls": [(r["strike"], r.get("oi")) for r in top_p]}


def view(bars, facts=None, today=None):
    r = RULES
    facts = facts or {}
    cl = [b.c for b in bars]
    e20, e50 = ta.ema(cl, 20), ta.ema(cl, 50)
    a = ta.last(ta.atr(bars, 14))
    sup, res = ta.support_resistance(bars, 3, 1.0, 250)
    last = bars[-1].c
    trend = "unknown"
    if e50[-1] is not None:
        trend = "up" if last > e20[-1] > e50[-1] else "down" if last < e20[-1] < e50[-1] else "sideways"
    head, cond = [], []
    vix, pcr = ta.f(facts.get("india_vix")), ta.f(facts.get("pcr"))
    if vix is not None:
        if vix >= r["vix_high"]:
            cond.append("India VIX %.1f is high: options are expensive, a buyer needs a big move and risks volatility falling after events" % vix)
        elif vix <= r["vix_low"]:
            cond.append("India VIX %.1f is low: options are cheap, but a sudden volatility jump is possible" % vix)
        else:
            cond.append("India VIX %.1f is in the middle range" % vix)
    if pcr is not None:
        cond.append("put/call ratio %.2f (%s)" % (pcr, "leans bullish" if pcr >= r["pcr_bullish"] else "leans bearish" if pcr <= r["pcr_bearish"] else "neutral"))
    for k in ("sector_trend", "sentiment"):
        if facts.get(k):
            cond.append("%s: %s" % (k.replace("_", " "), facts[k]))
    today = today or datetime.date.fromisoformat(bars[-1].d[:10])
    for e in facts.get("events") or []:
        try:
            d = datetime.date.fromisoformat(str(e.get("date"))[:10])
        except ValueError:
            continue
        n = (d - today).days
        if 0 <= n <= r["event_days"]:
            head.append("%s in %d day(s): moves can gap, and option prices drop (volatility crush) once it passes" % (e.get("kind", "event"), n))
    return {"last": last, "trend": trend, "atr": round(a, 2),
            "expected_move_1d": round(a, 2), "expected_move_5d": round(a * math.sqrt(5), 2),
            "support": [round(z["mid"], 2) for z in sorted([z for z in sup if z["mid"] < last], key=lambda z: -z["mid"])[:3]],
            "resistance": [round(z["mid"], 2) for z in sorted([z for z in res if z["mid"] > last], key=lambda z: z["mid"])[:3]],
            "conditions": cond, "headwinds": head}


# ---------------------------------------------------------------- commands

def _opt_args(p):
    p.add_argument("--spot", type=float, required=True); p.add_argument("--strike", type=float, required=True)
    p.add_argument("--type", choices=["call", "put"], required=True); p.add_argument("--dte", type=float, required=True)
    p.add_argument("--iv", type=float); p.add_argument("--price", type=float); p.add_argument("--rate", type=float, default=6.5)


def cmd_greeks(a):
    iv = a.iv
    if iv is None and a.price is None:
        raise ta.TaError("give --iv (percent) or --price (the premium) to solve it")
    if iv is None:
        iv = implied_vol(a.price, a.spot, a.strike, a.dte, a.type, a.rate)
        print("implied volatility from the premium: %.2f%%" % iv)
    g = bs(a.spot, a.strike, a.dte, iv, a.type, a.rate)
    intrinsic = max(a.spot - a.strike, 0) if a.type == "call" else max(a.strike - a.spot, 0)
    print("%s %s  spot %.2f  %.0f days  IV %.2f%%" % (a.strike, a.type, a.spot, a.dte, iv))
    print("  model price   %.2f  (intrinsic %.2f, time value %.2f)" % (g["price"], intrinsic, g["price"] - intrinsic))
    print("  delta         %+.3f   (about a %.0f%% chance of finishing in the money, and the move per 1 point of the underlying)" % (g["delta"], abs(g["delta"]) * 100))
    print("  gamma         %.5f  (how fast delta changes; high near expiry at the strike)" % g["gamma"])
    print("  theta         %.2f per day (time decay a buyer pays, a seller earns)" % g["theta_day"])
    print("  vega          %.2f per 1 volatility point" % g["vega_pt"])
    print("  rho           %.2f per 1%% rate change" % g["rho_pt"])
    be = a.strike + g["price"] if a.type == "call" else a.strike - g["price"]
    print("  breakeven at expiry (buyer, at the model price): %.2f" % be)
    return 0


def cmd_decay(a):
    if a.iv is None:
        raise ta.TaError("give --iv (percent)")
    base = bs(a.spot, a.strike, a.dte, a.iv, a.type, a.rate)["price"]
    print("%s %s, spot held flat at %.2f, IV %.1f%%. Premium today %.2f." % (a.strike, a.type, a.spot, a.iv, base))
    print("  days left   premium   lost so far   theta/day")
    marks = sorted({int(a.dte)} | {d for d in (21, 14, 10, 7, 5, 3, 2, 1) if d < a.dte}, reverse=True)
    for d in marks:
        g = bs(a.spot, a.strike, d, a.iv, a.type, a.rate)
        print("  %9d   %7.2f   %9.0f%%   %9.2f" % (d, g["price"], (1 - g["price"] / base) * 100 if base else 0, g["theta_day"]))
    print("Decay speeds up as expiry nears (about the last %d days). Buyers fight time; sellers earn it but carry large risk." % RULES["theta_fast_dte"])
    return 0


def cmd_payoff(a):
    legs = parse_legs(a.legs)
    s = payoff_summary(legs, a.lot, a.spot)
    print("Legs: " + "; ".join("%s %s %g @ %g x%d" % l for l in legs) + "   lot %d" % a.lot)
    print("  net premium %s %.2f" % ("paid" if s["net_premium_paid"] > 0 else "received", abs(s["net_premium_paid"])))
    print("  max profit  %s" % ("UNLIMITED" if s["unlimited_profit"] else "%.2f" % s["max_profit"]))
    print("  max loss    %s" % ("UNLIMITED (the risk is not capped)" if s["unlimited_loss"] else "%.2f" % s["max_loss"]))
    print("  breakeven   %s" % (", ".join("%.2f" % b for b in s["breakevens"]) or "none"))
    if a.spot:
        print("  payoff at expiry, by underlying price:")
        for pct in (-5, -3, -1, 0, 1, 3, 5):
            p = a.spot * (1 + pct / 100.0)
            print("    %+d%%  %9.2f  ->  %12.2f" % (pct, p, payoff_at(legs, a.lot, p)))
    print("Premiums are yours to read from Kite; charges, margin and slippage are not included.")
    return 0


def cmd_chain(a):
    rows = json.loads(ta.read_text(a.file))
    c = chain_read(rows, a.spot)
    print("put/call ratio (by OI): %s" % c["pcr"])
    print("max pain strike: %s" % c["max_pain"])
    print("call OI walls (likely resistance): " + ", ".join("%g (%s)" % x for x in c["call_walls"]))
    print("put OI walls (likely support): " + ", ".join("%g (%s)" % x for x in c["put_walls"]))
    print("OI walls and max pain are tendencies, not guarantees; they move as positions change.")
    return 0


def cmd_view(a):
    facts = json.loads(ta.read_text(a.facts)) if a.facts else {}
    bars = ta.load_bars(a.bars)
    v = view(bars, facts)
    name = facts.get("symbol", "underlying")
    print("%s  last %.2f | daily trend %s | ATR %.2f" % (name, v["last"], v["trend"], v["atr"]))
    print("  typical move: about %.2f a day, %.2f over 5 days (ATR based): compare this with the premium you would pay" % (v["expected_move_1d"], v["expected_move_5d"]))
    print("  support    " + (", ".join("%.2f" % x for x in v["support"]) or "none found"))
    print("  resistance " + (", ".join("%.2f" % x for x in v["resistance"]) or "none found"))
    for c in v["conditions"]:
        print("  ~ " + c)
    for h in v["headwinds"]:
        print("  ! " + h)
    print("Research only. Orders for F&O are not placed or approved by this toolkit.")
    return 0



# ---------------------------------------------------------------- volatility: HV vs IV, IV rank and percentile

def vol_read(closes, iv, iv_history=None, event_days=None):
    """-> dict. closes: underlying daily closes. iv: current implied vol (percent). iv_history: past IV or India VIX closes."""
    out = {"iv": iv, "notes": []}
    hv20, hv60 = ta.hist_vol(closes, 20), ta.hist_vol(closes, 60)
    out["hv20"], out["hv60"] = (round(hv20, 1) if hv20 else None), (round(hv60, 1) if hv60 else None)
    if hv20:
        prem = iv - hv20
        out["iv_minus_hv20"] = round(prem, 1)
        out["notes"].append("IV is %.1f points %s realised volatility (20 days): options are %s versus how the underlying has actually moved" %
                            (abs(prem), "above" if prem > 0 else "below", "rich (favours sellers, if the move stays small)" if prem > 0 else "cheap (favours buyers)"))
    h = [x for x in (iv_history or []) if x is not None]
    if len(h) >= 60:
        out["iv_rank"] = round(ta.range_rank(iv, h), 1)
        out["iv_percentile"] = round(ta.percentile_rank(iv, h), 1)
        out["notes"].append("IV rank %.0f and percentile %.0f over %d observations: " % (out["iv_rank"], out["iv_percentile"], len(h)) +
                            ("high, so premium is expensive; selling strategies get paid more but carry the tail risk" if out["iv_percentile"] >= 70 else
                             "low, so premium is cheap; buying strategies cost less and benefit if volatility rises" if out["iv_percentile"] <= 30 else "middling: no strong volatility edge"))
        out["notes"].append("India VIX history is a proxy for index IV; for a single stock use that stock's own IV history")
    else:
        out["notes"].append("IV rank needs at least 60 past IV/VIX readings; none given, so rank is not shown")
    if event_days is not None and event_days >= 0:
        out["notes"].append("an event is %d day(s) away: IV usually runs up into it and drops sharply after (volatility crush). A long option can lose even if the stock moves the right way" % event_days)
    return out


# ---------------------------------------------------------------- strategy templates with model prices

STRATS = ("straddle", "strangle", "bull-call", "bear-put", "iron-condor", "iron-butterfly")


def _px(spot, k, dte, iv, kind):
    p = bs(spot, k, dte, iv, kind)["price"]
    return max(round(round(p / 0.05) * 0.05, 2), 0.05)


def build_strategy(name, spot, step, dte, iv, width=None, sell=False):
    """-> legs [(side, kind, strike, premium, lots)] priced by Black-Scholes (an approximation: use the live quotes before trading)."""
    if name not in STRATS:
        raise ta.TaError("strategy must be one of: " + ", ".join(STRATS))
    atm = round(spot / step) * step
    w = width or 2 * step
    P = lambda k, kind: _px(spot, k, dte, iv, kind)
    if name == "straddle":
        sd = "sell" if sell else "buy"
        return [(sd, "call", atm, P(atm, "call"), 1), (sd, "put", atm, P(atm, "put"), 1)]
    if name == "strangle":
        sd = "sell" if sell else "buy"
        return [(sd, "call", atm + w, P(atm + w, "call"), 1), (sd, "put", atm - w, P(atm - w, "put"), 1)]
    if name == "bull-call":
        return [("buy", "call", atm, P(atm, "call"), 1), ("sell", "call", atm + w, P(atm + w, "call"), 1)]
    if name == "bear-put":
        return [("buy", "put", atm, P(atm, "put"), 1), ("sell", "put", atm - w, P(atm - w, "put"), 1)]
    if name == "iron-butterfly":
        return [("sell", "call", atm, P(atm, "call"), 1), ("sell", "put", atm, P(atm, "put"), 1),
                ("buy", "call", atm + w, P(atm + w, "call"), 1), ("buy", "put", atm - w, P(atm - w, "put"), 1)]
    return [("sell", "call", atm + w, P(atm + w, "call"), 1), ("sell", "put", atm - w, P(atm - w, "put"), 1),
            ("buy", "call", atm + 2 * w, P(atm + 2 * w, "call"), 1), ("buy", "put", atm - 2 * w, P(atm - 2 * w, "put"), 1)]


def net_greeks(legs, lot, spot, dte, iv):
    tot = {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "vega_pt": 0.0}
    for side, kind, k, _, n in legs:
        g = bs(spot, k, dte, iv, kind)
        sign = 1 if side == "buy" else -1
        for key in tot:
            tot[key] += sign * g[key] * lot * n
    return {k: round(v, 4) for k, v in tot.items()}


def reprice(legs, lot, spot, dte, iv):
    """Mark-to-model value change of the position versus the entry premiums."""
    pnl = 0.0
    for side, kind, k, prem, n in legs:
        now = bs(spot, k, max(dte, 1e-6), max(iv, 1.0), kind)["price"]
        pnl += ((now - prem) if side == "buy" else (prem - now)) * lot * n
    return pnl


# ---------------------------------------------------------------- stress test and capital cap

def stress(legs, lot, spot, dte, iv, capital=None, max_loss_pct=2.0):
    spots = [-5, -3, -2, -1, 0, 1, 2, 3, 5]
    ivs = [-5, 0, 5]
    grid = {}
    for sp in spots:
        for dv in ivs:
            grid[(sp, dv)] = reprice(legs, lot, spot * (1 + sp / 100.0), dte, iv + dv)
    gap_next = min(reprice(legs, lot, spot * (1 + sp / 100.0), max(dte - 1, 1e-6), iv + dv) for sp in (-4, 4) for dv in (0, 8))
    summ = payoff_summary(legs, lot, spot)
    worst = min(grid.values())
    out = {"grid": grid, "worst_model": round(worst, 2), "gap_loss_overnight": round(gap_next, 2),
           "expiry_max_loss": summ["max_loss"], "unlimited_loss": summ["unlimited_loss"], "notes": []}
    if summ["unlimited_loss"]:
        out["notes"].append("this position has UNLIMITED loss: it needs a hard stop or a hedge leg, and the margin can jump in a fast move")
    if capital:
        cap = capital * max_loss_pct / 100.0
        out["loss_cap"] = round(cap, 2)
        known = [x for x in (out["worst_model"], out["gap_loss_overnight"], out["expiry_max_loss"]) if x is not None]
        worst_any = min(known)
        out["within_cap"] = (-worst_any) <= cap and not summ["unlimited_loss"]
        out["notes"].append("loss cap %.0f%% of capital = Rs %.0f; worst case found Rs %.0f -> %s" %
                            (max_loss_pct, cap, -worst_any, "WITHIN the cap" if out["within_cap"] else "OVER the cap: shrink the size or add a hedge leg"))
    out["notes"].append("SPAN + exposure margin cannot be computed here: read it in Zerodha's margin calculator (or the Kite margin tool) before placing the order")
    out["notes"].append("a position held overnight can gap through any stop; the gap row above is the minimum you must be able to afford")
    return out


def render_stress(st):
    L = ["STRESS (model values, today's date, entry premiums as cost): profit or loss in Rs"]
    L.append("    spot move   IV -5      IV same    IV +5")
    for sp in (-5, -3, -2, -1, 0, 1, 2, 3, 5):
        L.append("    %+4d%%    %9.0f  %9.0f  %9.0f" % (sp, st["grid"][(sp, -5)], st["grid"][(sp, 0)], st["grid"][(sp, 5)]))
    L.append("    overnight gap (+-4%% with IV flat or +8) worst: Rs %.0f" % st["gap_loss_overnight"])
    if st["expiry_max_loss"] is not None:
        L.append("    maximum loss at expiry: Rs %.0f" % st["expiry_max_loss"])
    for n in st["notes"]:
        L.append("    ~ " + n)
    return "\n".join(L)


# ---------------------------------------------------------------- market depth and OI buildup

def depth_read(q, qty=None):
    """q: a Kite quote {"last_price", "depth": {"buy":[{price,quantity,orders}], "sell":[...]}}."""
    d = q.get("depth") or {}
    buy, sell = d.get("buy") or [], d.get("sell") or []
    if not buy or not sell:
        raise ta.TaError("the quote has no depth; ask Kite for get_quotes (not get_ltp)")
    bid, ask = buy[0]["price"], sell[0]["price"]
    mid = (bid + ask) / 2.0
    bq, sq = sum(x["quantity"] for x in buy), sum(x["quantity"] for x in sell)
    out = {"bid": bid, "ask": ask, "spread": round(ask - bid, 2), "spread_pct": round((ask - bid) / mid * 100, 2) if mid else None,
           "buy_qty": bq, "sell_qty": sq, "imbalance": round((bq - sq) / (bq + sq), 2) if bq + sq else 0.0, "notes": []}
    if qty:
        def walk(levels, side):
            need, cost = qty, 0.0
            for lv in levels:
                t = min(need, lv["quantity"])
                cost += t * lv["price"]
                need -= t
                if need <= 0:
                    return cost / qty
            return None
        fill_buy, fill_sell = walk(sell, "buy"), walk(buy, "sell")
        if fill_buy is None or fill_sell is None:
            out["notes"].append("the visible 5 levels cannot absorb %d: size is bigger than the book, expect large slippage" % qty)
        else:
            out["buy_fill"], out["sell_fill"] = round(fill_buy, 2), round(fill_sell, 2)
            out["impact_cost_pct"] = round(((fill_buy - mid) / mid + (mid - fill_sell) / mid) / 2 * 100, 2)
            out["notes"].append("filling %d lots-worth now costs about %.2f%% versus the mid price (round trip, one side averaged)" % (qty, out["impact_cost_pct"]))
    if out["spread_pct"] is not None and out["spread_pct"] > 2.0:
        out["notes"].append("the spread is wide (%.1f%%): trade a more liquid strike or use limit orders only" % out["spread_pct"])
    if abs(out["imbalance"]) >= 0.4:
        out["notes"].append("the top-5 book is lopsided toward %s; a hint of short-term pressure, not a signal" % ("buyers" if out["imbalance"] > 0 else "sellers"))
    return out


def buildup(price_change, oi_change):
    if oi_change is None or price_change is None:
        return "unknown"
    if price_change > 0 and oi_change > 0:
        return "long buildup (fresh buying)"
    if price_change < 0 and oi_change > 0:
        return "short buildup (fresh selling)"
    if price_change > 0 and oi_change < 0:
        return "short covering (shorts exiting)"
    if price_change < 0 and oi_change < 0:
        return "long unwinding (longs exiting)"
    return "flat"


def buildup_rows(rows, top=5):
    """rows: option rows with price_change and oi_change (both numbers) -> biggest OI changes, classified."""
    done = [dict(r, tag=buildup(r.get("price_change"), r.get("oi_change"))) for r in rows if r.get("oi_change")]
    done.sort(key=lambda r: -abs(r["oi_change"]))
    return done[:top]


def cmd_vol(a):
    closes = [b.c for b in ta.load_bars(a.bars)]
    hist = None
    if a.iv_history:
        raw = json.loads(ta.read_text(a.iv_history))
        hist = [float(x["close"] if isinstance(x, dict) else x[4] if isinstance(x, list) else x) for x in (raw.get("candles", raw) if isinstance(raw, dict) else raw)]
    v = vol_read(closes, a.iv, hist, a.event_days)
    print("VOLATILITY  IV %.1f | HV20 %s | HV60 %s" % (a.iv, v["hv20"], v["hv60"]))
    if "iv_rank" in v:
        print("    IV rank %.0f | IV percentile %.0f" % (v["iv_rank"], v["iv_percentile"]))
    for n in v["notes"]:
        print("    ~ " + n)
    return 0


def cmd_strategy(a):
    legs = build_strategy(a.name, a.spot, a.step, a.dte, a.iv, a.width, a.sell)
    print("%s%s (model prices at IV %.1f%%, %.0f days; replace with live quotes)" % ("short " if a.sell and a.name in ("straddle", "strangle") else "", a.name, a.iv, a.dte))
    for side, kind, k, prem, n in legs:
        print("    %-4s %-4s %g @ %.2f" % (side, kind, k, prem))
    sm = payoff_summary(legs, a.lot, a.spot)
    print("    max profit %s | max loss %s | breakevens %s | net premium %s" %
          ("unlimited" if sm["unlimited_profit"] else sm["max_profit"], "UNLIMITED" if sm["unlimited_loss"] else sm["max_loss"], sm["breakevens"], "%.0f %s" % (abs(sm["net_premium_paid"]), "paid (debit)" if sm["net_premium_paid"] > 0 else "received (credit)")))
    g = net_greeks(legs, a.lot, a.spot, a.dte, a.iv)
    print("    net delta %+.1f | gamma %+.4f | theta %+.0f/day | vega %+.0f per vol point" % (g["delta"], g["gamma"], g["theta_day"], g["vega_pt"]))
    print("    Not built: calendar and ratio spreads (they need two expiries or uneven lots).")
    print("    legs string for payoff/stress: " + "; ".join("%s %s %g %.2f" % (s_, k_, x, p) for s_, k_, x, p, _ in legs))
    return 0


def cmd_stress(a):
    legs = parse_legs(a.legs)
    st = stress(legs, a.lot, a.spot, a.dte, a.iv, a.capital, a.max_loss_pct)
    print(render_stress(st))
    return 0 if st.get("within_cap", True) else 2


def cmd_depth(a):
    q = json.loads(ta.read_text(a.file))
    if isinstance(q, dict) and "depth" not in q and len(q) == 1:
        q = list(q.values())[0]
    d = depth_read(q, a.qty)
    print("DEPTH bid %.2f | ask %.2f | spread %.2f (%.2f%%) | buy qty %d | sell qty %d | imbalance %+.2f" %
          (d["bid"], d["ask"], d["spread"], d["spread_pct"] or 0, d["buy_qty"], d["sell_qty"], d["imbalance"]))
    for n in d["notes"]:
        print("    ~ " + n)
    return 0


def cmd_buildup(a):
    rows = json.loads(ta.read_text(a.file))
    for r in buildup_rows(rows, a.top):
        print("    %g %-4s OI change %+d, price change %+.2f -> %s" % (r["strike"], r["type"], r["oi_change"], r.get("price_change") or 0, r["tag"]))
    print("    Buildup labels describe what happened; they do not predict. Read them with the chart.")
    return 0

def cmd_rules(a):
    print(json.dumps(RULES, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="F&O research tools (trader-fno).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("greeks"); _opt_args(p)
    p = sub.add_parser("decay"); _opt_args(p)
    p = sub.add_parser("payoff"); p.add_argument("--legs", required=True); p.add_argument("--lot", type=int, required=True)
    p.add_argument("--spot", type=float)
    p = sub.add_parser("chain"); p.add_argument("file"); p.add_argument("--spot", type=float)
    p = sub.add_parser("view"); p.add_argument("bars"); p.add_argument("--facts")
    p = sub.add_parser("vol"); p.add_argument("bars"); p.add_argument("--iv", type=float, required=True)
    p.add_argument("--iv-history"); p.add_argument("--event-days", type=int)
    p = sub.add_parser("strategy"); p.add_argument("name", choices=STRATS); p.add_argument("--spot", type=float, required=True)
    p.add_argument("--step", type=float, required=True); p.add_argument("--dte", type=float, required=True); p.add_argument("--iv", type=float, required=True)
    p.add_argument("--lot", type=int, required=True); p.add_argument("--width", type=float); p.add_argument("--sell", action="store_true")
    p = sub.add_parser("stress"); p.add_argument("--legs", required=True); p.add_argument("--lot", type=int, required=True)
    p.add_argument("--spot", type=float, required=True); p.add_argument("--dte", type=float, required=True); p.add_argument("--iv", type=float, required=True)
    p.add_argument("--capital", type=float); p.add_argument("--max-loss-pct", type=float, default=2.0)
    p = sub.add_parser("depth"); p.add_argument("file"); p.add_argument("--qty", type=int)
    p = sub.add_parser("buildup"); p.add_argument("file"); p.add_argument("--top", type=int, default=5)
    sub.add_parser("rules")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"greeks": cmd_greeks, "decay": cmd_decay, "payoff": cmd_payoff, "chain": cmd_chain,
                "view": cmd_view, "vol": cmd_vol, "strategy": cmd_strategy, "stress": cmd_stress,
                "depth": cmd_depth, "buildup": cmd_buildup, "rules": cmd_rules}[a.cmd](a)
    except (ta.TaError, ValueError, KeyError, OSError) as e:
        print("fno.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
