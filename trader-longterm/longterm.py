#!/usr/bin/env python3
"""Long-term investing read for trader-longterm. Standard library only, no network.

Two questions, answered separately and then put together:
  1. FUNDAMENTALS: is this a business worth owning for years? (quality, growth, balance sheet,
     valuation, ownership) -> CANDIDATE / WATCH / AVOID.
  2. ENTRY: is the PRICE in a zone where a long-term buyer has historically been rewarded? Uses
     weekly and monthly bars only: the 200-day / 40-week average, multi-year support zones, the
     fall from the high and weekly RSI. Daily noise, news and market mood are ignored on purpose.

    python3 longterm.py read facts.json bars.json        # bars = DAILY OHLC, 2+ years (5+ is better)
    python3 longterm.py zones bars.json                  # technical entry view only
    python3 longterm.py rules

facts.json is the fundamentals JSON (shape in the docstring of fundamentals below). bars.json is
daily OHLC from Kite's historical data (day interval) or a CSV with date,open,high,low,close,volume.

A research read, not advice and not a price target. Education and tooling only.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ta  # noqa: E402

f = ta.f


class AnalysisError(ta.TaError):
    pass


FACTS_SHAPE = """
  {"symbol": "COFORGE", "asof": "2026-10-24", "financial_company": false,
   "price": {"last": 1190, "high_52w": 1611.8, "low_52w": 989.57},
   "returns": {"ytd_pct": -17.8, "cagr_5y_pct": 12.0},
   "valuation": {"pe": 21.9, "industry_pe": 25, "pb": 1.5, "industry_pb": 1.3, "div_yield_pct": 0.5},
   "growth": {"revenue_3y_pct": 6.9, "eps_3y_pct": 10.1, "cashflow_3y_pct": 18.6},
   "quality": {"roe_pct": 7.7, "net_margin_pct": 7.4, "cash_flow_margin_pct": 17.7},
   "balance": {"debt": 402962, "equity": 1085866, "cash": 243408},
   "ownership": {"promoter_pct": 50.4, "promoter_change": -0.04, "fii_change": -0.5, "dii_change": 0.5},
   "red_flags": {"asm": false, "gsm": false, "pledged": false, "default_risk": false},
   "analyst": {"count": 24, "buy_pct": 100, "upside_pct": 42.0}}
"""

RULES = {
    "roe_strong": 15.0, "roe_ok": 10.0,
    "growth_strong": 12.0, "growth_ok": 6.0,
    "de_strong": 0.5, "de_ok": 1.0, "de_weak": 1.5,
    "pe_cheap_ratio": 0.9, "pe_rich_ratio": 1.3,
    "pb_rich_ratio": 1.5,
    "promoter_drop_pts": -1.0, "fii_drop_pts": -1.0,
    "near_high_pct": 5.0,
    "deep_discount_pct": 30.0,
}
RULES.update({"cash_conv_strong": 0.9, "cash_conv_ok": 0.6, "fcf_pos_strong": 0.8, "fcf_pos_ok": 0.6,
              "moat_strong": 6, "moat_ok": 3})
RANK = {"Strong": 2, "OK": 1, "Weak": 0, "Unknown": None}
ORDER = ["Quality", "Growth", "Cash flow", "Balance sheet", "Valuation", "Moat", "Ownership"]


def pillars(facts):
    """-> {pillar: (rating, [reasons])}"""
    r = RULES
    out = {}

    q = facts.get("quality") or {}
    roe, nm, cfm = f(q.get("roe_pct")), f(q.get("net_margin_pct")), f(q.get("cash_flow_margin_pct"))
    why = []
    if roe is None:
        rating = "Unknown"
    else:
        rating = "Strong" if roe >= r["roe_strong"] else "OK" if roe >= r["roe_ok"] else "Weak"
        why.append("return on equity %.1f%%" % roe)
        if cfm is not None and cfm <= 0:
            rating = "Weak"
            why.append("operating cash flow margin is not positive")
    if nm is not None:
        why.append("net margin %.1f%%" % nm)
    out["Quality"] = (rating, why)

    g = facts.get("growth") or {}
    rev, eps = f(g.get("revenue_3y_pct")), f(g.get("eps_3y_pct"))
    vals = [v for v in (rev, eps) if v is not None]
    why = []
    if rev is not None:
        why.append("revenue growth %.1f%% a year over 3 years" % rev)
    if eps is not None:
        why.append("earnings per share growth %.1f%% a year" % eps)
    if not vals:
        rating = "Unknown"
    else:
        lo = min(vals)
        rating = "Strong" if lo >= r["growth_strong"] else "OK" if lo >= r["growth_ok"] else "Weak"
    out["Growth"] = (rating, why)

    b = facts.get("balance") or {}
    debt, eq, cash = f(b.get("debt")), f(b.get("equity")), f(b.get("cash"))
    why = []
    if facts.get("financial_company"):
        out["Balance sheet"] = ("Unknown", ["bank/NBFC: debt-to-equity is not meaningful, check capital adequacy and asset quality yourself"])
    elif debt is None or not eq or eq <= 0:
        out["Balance sheet"] = ("Unknown", [])
    else:
        de = debt / eq
        why.append("debt is %.2f times equity" % de)
        if cash is not None:
            why.append("net debt %s" % ("negative (net cash)" if debt - cash < 0 else "positive"))
        rating = "Strong" if de <= r["de_strong"] else "OK" if de <= r["de_ok"] else "Weak"
        if de > r["de_weak"]:
            why.append("heavily indebted")
        out["Balance sheet"] = (rating, why)

    v = facts.get("valuation") or {}
    pe, ipe, pb, ipb = f(v.get("pe")), f(v.get("industry_pe")), f(v.get("pb")), f(v.get("industry_pb"))
    why = []
    rating = "Unknown"
    if pe is not None and ipe:
        ratio = pe / ipe
        why.append("P/E %.1f vs industry %.1f" % (pe, ipe))
        rating = "Strong" if ratio <= r["pe_cheap_ratio"] else "OK" if ratio <= r["pe_rich_ratio"] else "Weak"
    elif pe is not None:
        why.append("P/E %.1f (no industry figure)" % pe)
    if pb is not None and ipb:
        why.append("P/B %.2f vs industry %.2f" % (pb, ipb))
        if pb / ipb > r["pb_rich_ratio"] and rating == "Strong":
            rating = "OK"
    p = facts.get("price") or {}
    last, hi, lo = f(p.get("last")), f(p.get("high_52w")), f(p.get("low_52w"))
    if last and hi:
        off = (hi - last) / hi * 100.0
        why.append("%.0f%% below its 52-week high" % off)
    out["Valuation"] = (rating, why)

    o = facts.get("ownership") or {}
    pc, fc, fl = f(o.get("promoter_change")), f(o.get("fii_change")), facts.get("red_flags") or {}
    why, rating = [], "Unknown"
    if pc is not None or fc is not None or o.get("promoter_pct") is not None:
        rating = "OK"
        if o.get("promoter_pct") is not None:
            why.append("promoters hold %.1f%%" % float(o["promoter_pct"]))
        if pc is not None and pc <= r["promoter_drop_pts"]:
            rating = "Weak"
            why.append("promoter stake fell %.2f points last quarter" % abs(pc))
        if fc is not None and fc <= r["fii_drop_pts"]:
            rating = "Weak"
            why.append("foreign institutions cut %.2f points" % abs(fc))
        if fl.get("pledged"):
            rating = "Weak"
            why.append("a large part of promoter shares is pledged")
        if rating == "OK" and (pc or 0) > 0 and (fc or 0) >= 0:
            rating = "Strong"
    out["Ownership"] = (rating, why)

    fin = facts.get("financials") or {}
    cfo, ni = fin.get("cfo") or [], fin.get("net_profit") or []
    fcf = fin.get("fcf") or ([c - x for c, x in zip(cfo, fin.get("capex") or [])] if fin.get("capex") else [])
    rev = fin.get("revenue") or []
    why, rating = [], "Unknown"
    if cfo and ni and sum(ni) > 0:
        conv = sum(cfo[-len(ni):]) / sum(ni[-len(cfo):])
        pos = (sum(1 for x in fcf if x > 0) / len(fcf)) if fcf else None
        why.append("operating cash flow is %.1f times net profit over %d years" % (conv, min(len(cfo), len(ni))))
        if pos is not None:
            why.append("free cash flow positive in %d of %d years" % (sum(1 for x in fcf if x > 0), len(fcf)))
        if conv >= r["cash_conv_strong"] and (pos is None or pos >= r["fcf_pos_strong"]):
            rating = "Strong"
        elif conv >= r["cash_conv_ok"] and (pos is None or pos >= r["fcf_pos_ok"]):
            rating = "OK"
        else:
            rating = "Weak"
    if len(rev) >= 3 and rev[0] > 0 and len(ni) >= 3:
        yrs = len(rev) - 1
        why.append("revenue grew %.1f%% a year over %d years" % (((rev[-1] / rev[0]) ** (1.0 / yrs) - 1) * 100, yrs))
        if ni[0] > 0 and ni[-1] > 0:
            why.append("net margin went from %.1f%% to %.1f%%" % (ni[0] / rev[0] * 100, ni[-1] / rev[-1] * 100))
    out["Cash flow"] = (rating, why)

    m = facts.get("moat") or {}
    keys = ("network_effects", "brand", "switching_costs", "cost_advantage", "scale")
    scores = [f(m.get(k)) for k in keys if f(m.get(k)) is not None]
    if not scores:
        out["Moat"] = ("Unknown", [])
    elif not str(m.get("evidence") or "").strip():
        out["Moat"] = ("Unknown", ["moat scores were given without evidence, so they are ignored: say why the business is hard to compete with"])
    else:
        tot = sum(scores)
        rating = "Strong" if tot >= r["moat_strong"] else "OK" if tot >= r["moat_ok"] else "Weak"
        out["Moat"] = (rating, ["moat score %d of %d: %s" % (tot, 2 * len(scores), str(m["evidence"]).strip()[:160])])
    return out


def judge(facts):
    P = pillars(facts)
    flags = facts.get("red_flags") or {}
    reasons, notes, missing = [], [], []
    skip = [k for k in ("asm", "gsm", "default_risk") if flags.get(k)]
    for k, (rating, why) in P.items():
        if rating == "Unknown" and not why:
            missing.append(k.lower())
    rated = {k: v[0] for k, v in P.items() if RANK[v[0]] is not None}
    weak = [k for k, v in rated.items() if v == "Weak"]
    core_weak = [k for k in ("Quality", "Growth", "Cash flow", "Balance sheet") if rated.get(k) == "Weak"]
    if skip:
        stance = "AVOID"
        reasons.append("surveillance/default red flag: " + ", ".join(skip))
    elif len(core_weak) >= 2:
        stance = "AVOID"
        reasons.append("weak on " + " and ".join(core_weak))
    elif len(rated) < 3:
        stance = "WATCH"
        reasons.append("too little data to form a view (only %d of 5 pillars rated)" % len(rated))
    elif weak == ["Valuation"]:
        stance = "WATCH"
        reasons.append("business looks sound but the price looks rich against its industry")
    elif weak:
        stance = "WATCH"
        reasons.append("weak on " + ", ".join(weak))
    else:
        stance = "CANDIDATE"
        reasons.append("no weak pillar among the rated ones")
    p = facts.get("price") or {}
    last, hi, lo = f(p.get("last")), f(p.get("high_52w")), f(p.get("low_52w"))
    if last and hi and lo and hi > lo:
        if (hi - last) / hi * 100 >= RULES["deep_discount_pct"]:
            notes.append("price is far below its 52-week high: find out why before calling it cheap")
        if (hi - last) / hi * 100 <= RULES["near_high_pct"]:
            notes.append("price is near its 52-week high")
    a = facts.get("analyst")
    if a:
        notes.append("analysts: %s ratings, %s%% buy, average upside %s%% (opinions, not a forecast)"
                     % (a.get("count", "?"), a.get("buy_pct", "?"), a.get("upside_pct", "?")))
    rt = facts.get("returns") or {}
    if f(rt.get("cagr_5y_pct")) is not None:
        notes.append("5-year return %.1f%% a year" % float(rt["cagr_5y_pct"]))
    ind = facts.get("industry") or {}
    if ind.get("tailwinds"):
        notes.append("industry tailwinds: " + "; ".join(str(x) for x in ind["tailwinds"]))
    if ind.get("headwinds"):
        notes.append("industry headwinds: " + "; ".join(str(x) for x in ind["headwinds"]))
    if ind.get("cyclical"):
        notes.append("a cyclical industry: earnings swing with the cycle, so judge valuation on average, not peak, earnings")
    notes.append("peer/industry comparisons depend on how Tickertape classifies the company: check the peers yourself")
    return {"stance": stance, "reasons": reasons, "pillars": {k: {"rating": v[0], "why": v[1]} for k, v in P.items()},
            "notes": notes, "not_checked": missing}




# ---------------------------------------------------------------- entry zones (technical, long-term)

TECH = {
    "zone_tol_pct": 4.0,          # weekly pivot lows within 4% form one support zone
    "near_zone_pct": 3.0,         # "at the zone" = within 3% above the zone's top
    "deep_drawdown_pct": 30.0,    # fall from the high that marks a deep drawdown
    "rsi_oversold": 40.0,         # weekly RSI at or below this
    "rsi_hot": 70.0,
    "extended_pct": 30.0,         # more than 30% above the 200-day average
    "min_touches": 2,
    "tranches": 3,
}


def entry_view(bars):
    """Weekly/monthly technical read of where a long-term buyer is rewarded. Pure function."""
    if len(bars) < 120:
        raise AnalysisError("need at least ~120 daily bars (about 6 months); 2+ years is better")
    closes = [b.c for b in bars]
    last = closes[-1]
    wk, mo = ta.resample(bars, "W"), ta.resample(bars, "M")
    sma200 = ta.last(ta.sma(closes, 200))
    sma40w = ta.last(ta.sma([b.c for b in wk], 40))
    rsi_w = ta.last(ta.rsi([b.c for b in wk], 14))
    rsi_m = ta.last(ta.rsi([b.c for b in mo], 14)) if len(mo) > 15 else None
    dd, high = ta.drawdown_pct(bars)
    sup, res = ta.support_resistance(wk, k=4, tol_pct=TECH["zone_tol_pct"])
    below = [z for z in sup if z["high"] <= last * 1.0001]
    below.sort(key=lambda z: z["high"], reverse=True)
    near = [z for z in sup if z["low"] * 0.98 <= last <= z["high"] * (1 + TECH["near_zone_pct"] / 100.0)]
    avg = sma200 or sma40w
    ext = (last / avg - 1) * 100.0 if avg else None
    state, why = "NEUTRAL", []
    strong_near = [z for z in near if z["touches"] >= TECH["min_touches"]]
    if strong_near:
        state = "AT SUPPORT"
        z = max(strong_near, key=lambda z: z["touches"])
        why.append("price is at a weekly support zone %.2f to %.2f that held %d times" % (z["low"], z["high"], z["touches"]))
    if dd >= TECH["deep_drawdown_pct"] and rsi_w is not None and rsi_w <= TECH["rsi_oversold"]:
        state = "AT SUPPORT" if state == "AT SUPPORT" else "DEEP PULLBACK"
        why.append("%.0f%% below its high with weekly RSI %.0f (washed out)" % (dd, rsi_w))
    if (ext is not None and ext > TECH["extended_pct"]) or (rsi_w is not None and rsi_w >= TECH["rsi_hot"]):
        state = "EXTENDED"
        why = ["price is %s the 200-day average%s: a long-term buyer gets a poor price here"
               % ("%.0f%% above" % ext if ext is not None else "far above",
                  ", weekly RSI %.0f" % rsi_w if rsi_w is not None else "")]
    if not why:
        why.append("not at a long-term support zone and not stretched")
    ladder = []
    for z in below[:TECH["tranches"]]:
        ladder.append({"zone_low": round(z["low"], 2), "zone_high": round(z["high"], 2), "touches": z["touches"]})
    broken = below[-1]["low"] if below else None
    return {"state": state, "why": why, "last": round(last, 2), "high": round(high, 2), "drawdown_pct": round(dd, 1),
            "sma200": round(sma200, 2) if sma200 else None, "sma40w": round(sma40w, 2) if sma40w else None,
            "extension_pct": round(ext, 1) if ext is not None else None, "rsi_weekly": round(rsi_w, 1) if rsi_w else None,
            "rsi_monthly": round(rsi_m, 1) if rsi_m else None, "support_zones_below": ladder,
            "resistance_zones_above": [{"zone_low": round(z["low"], 2), "zone_high": round(z["high"], 2), "touches": z["touches"]}
                                      for z in sorted([z for z in res if z["low"] > last], key=lambda z: z["low"])[:3]],
            "recheck_below": round(broken * 0.92, 2) if broken else None}


def combine(fund, tech):
    """The two answers together. Fundamentals gate the decision; technicals choose the price."""
    s, st = fund["stance"], tech["state"]
    if s == "AVOID":
        return "NO ENTRY", "fundamentals say AVOID: a good price does not fix a weak business"
    if s == "WATCH" and fund.get("not_checked"):
        return "INCOMPLETE", "fundamentals are only partly checked; fill the gaps before buying"
    if st == "EXTENDED":
        return "WAIT", "the price is stretched; set alerts at the support zones below and wait"
    if s == "CANDIDATE" and st in ("AT SUPPORT", "DEEP PULLBACK"):
        return "ACCUMULATE ZONE", "good business at a price long-term buyers have been rewarded at: staged buying is reasonable to consider"
    if s == "WATCH" and st in ("AT SUPPORT", "DEEP PULLBACK"):
        return "WATCHLIST", "price is attractive but the fundamentals have a weak spot (%s): decide if you accept it" % "; ".join(fund["reasons"])
    return "WAIT", "no long-term entry signal yet"


def render_entry(t):
    lines = ["ENTRY VIEW: %s" % t["state"]]
    lines += ["    - " + w for w in t["why"]]
    lines.append("    last %.2f | high %.2f | %.1f%% below the high | 200-day avg %s | 40-week avg %s | RSI weekly %s monthly %s"
                 % (t["last"], t["high"], t["drawdown_pct"], t["sma200"], t["sma40w"], t["rsi_weekly"], t["rsi_monthly"]))
    if t["support_zones_below"]:
        lines.append("    support zones below (staged-buying ladder, nearest first):")
        for z in t["support_zones_below"]:
            lines.append("      %.2f to %.2f  (held %d times)" % (z["zone_low"], z["zone_high"], z["touches"]))
    if t["recheck_below"]:
        lines.append("    re-check the whole thesis if price closes below %.2f (8%% under the lowest zone above)" % t["recheck_below"])
    return "\n".join(lines)


IGNORED = ("Ignored on purpose: daily moves, market mood, results-day reactions and news headlines. "
           "Re-check only for a thesis-breaking change: governance trouble, a rating downgrade, ASM/GSM listing, rising pledge, "
           "or the business metrics above deteriorating.")



# ---------------------------------------------------------------- valuation models (DCF, reverse DCF, DDM, relative)

def dcf_per_share(fcf0, g_pct, r_pct, tg_pct, net_debt, shares, years=10, stage1=5):
    """Two-stage DCF. Growth g for `stage1` years, fading in a straight line to the terminal growth,
    then a Gordon terminal value. All money in rupees crore, shares in crore, so the result is Rs per share."""
    r, tg = r_pct / 100.0, tg_pct / 100.0
    if r <= tg:
        raise AnalysisError("the discount rate must be above the terminal growth")
    if fcf0 <= 0:
        raise AnalysisError("a DCF needs positive free cash flow; use relative valuation for this company")
    pv, cf = 0.0, fcf0
    for y in range(1, years + 1):
        g = g_pct if y <= stage1 else g_pct + (tg_pct - g_pct) * (y - stage1) / (years - stage1)
        cf *= 1 + g / 100.0
        pv += cf / (1 + r) ** y
    pv += cf * (1 + tg) / (r - tg) / (1 + r) ** years
    return (pv - net_debt) / shares


def implied_growth(price, fcf0, r_pct, tg_pct, net_debt, shares):
    """The 5-year growth rate that makes the DCF equal today's price (reverse DCF)."""
    lo, hi = -15.0, 45.0
    f_lo, f_hi = dcf_per_share(fcf0, lo, r_pct, tg_pct, net_debt, shares), dcf_per_share(fcf0, hi, r_pct, tg_pct, net_debt, shares)
    if not (f_lo <= price <= f_hi):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if dcf_per_share(fcf0, mid, r_pct, tg_pct, net_debt, shares) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def value_model(facts):
    """-> dict. Needs facts["valuation_inputs"]; every figure comes from the user's data, none is invented."""
    vi = facts.get("valuation_inputs") or {}
    price = f((facts.get("price") or {}).get("last"))
    out = {"notes": []}
    fcf0, shares = f(vi.get("fcf")), f(vi.get("shares"))
    nd = f(vi.get("net_debt"))
    if nd is None:
        b = facts.get("balance") or {}
        nd = (f(b.get("debt")) or 0) - (f(b.get("cash")) or 0) if b else 0.0
    r = f(vi.get("discount_pct")) or 12.0
    tg = f(vi.get("terminal_pct")) or 5.0
    g = f(vi.get("growth_pct"))
    if g is None:
        e = f((facts.get("growth") or {}).get("eps_3y_pct"))
        g = max(0.0, min(e, 15.0)) if e is not None else 8.0
        out["notes"].append("growth assumption %.1f%% was taken from 3-year EPS growth, capped at 15%%: change growth_pct to use your own" % g)
    out["assumptions"] = {"growth_pct": g, "discount_pct": r, "terminal_pct": tg, "net_debt": nd}
    if fcf0 and shares and fcf0 > 0:
        grid = {}
        for dg in (-3, 0, 3):
            for dr in (-1, 0, 1):
                grid[(g + dg, r + dr)] = dcf_per_share(fcf0, g + dg, r + dr, tg, nd, shares)
        vals = sorted(grid.values())
        out["dcf"] = {"base": grid[(g, r)], "low": vals[0], "high": vals[-1], "grid": {"%.0f/%.0f" % k: round(v, 1) for k, v in grid.items()}}
        if price:
            out["dcf"]["margin_of_safety_pct"] = round((out["dcf"]["base"] - price) / out["dcf"]["base"] * 100, 1)
            ig = implied_growth(price, fcf0, r, tg, nd, shares)
            out["reverse_dcf"] = {"implied_growth_pct": round(ig, 1) if ig is not None else None}
    else:
        out["notes"].append("no positive free cash flow or share count: DCF skipped")
    dps = f(vi.get("dps"))
    if dps and f(vi.get("dividend_growth_pct")) is not None and r > f(vi["dividend_growth_pct"]):
        dg_ = f(vi["dividend_growth_pct"])
        out["ddm"] = round(dps * (1 + dg_ / 100.0) / ((r - dg_) / 100.0), 1)
    mcap, ebitda = f(vi.get("market_cap")), f(vi.get("ebitda"))
    rel = {}
    if mcap and ebitda and ebitda > 0:
        rel["ev_ebitda"] = round((mcap + nd) / ebitda, 1)
        if f(vi.get("industry_ev_ebitda")):
            rel["industry_ev_ebitda"] = f(vi["industry_ev_ebitda"])
    pe, eg = f((facts.get("valuation") or {}).get("pe")), f(vi.get("eps_growth_pct")) or f((facts.get("growth") or {}).get("eps_3y_pct"))
    if pe and eg and eg > 0:
        rel["peg"] = round(pe / eg, 2)
    hist = [x for x in (vi.get("pe_history") or []) if f(x) is not None]
    if pe and len(hist) >= 20:
        rel["own_pe_percentile"] = round(100.0 * sum(1 for x in hist if x <= pe) / len(hist))
    out["relative"] = rel
    return out


def render_value(v):
    L = ["VALUATION MODELS (assumption-driven, a range and not a price target)"]
    a = v["assumptions"]
    L.append("    assumptions: growth %.1f%% for 5 years fading to %.1f%%, discount rate %.1f%%, net debt %.0f crore" % (a["growth_pct"], a["terminal_pct"], a["discount_pct"], a["net_debt"]))
    if "dcf" in v:
        d = v["dcf"]
        L.append("    DCF: %.0f per share (range %.0f to %.0f across growth +-3 and rate +-1)" % (d["base"], d["low"], d["high"]))
        if "margin_of_safety_pct" in d:
            L.append("    price vs base DCF: margin of safety %.0f%% (negative means the price is above the model)" % d["margin_of_safety_pct"])
        rd = v.get("reverse_dcf") or {}
        if rd.get("implied_growth_pct") is not None:
            L.append("    reverse DCF: today's price already assumes about %.1f%% a year free-cash-flow growth for 5 years" % rd["implied_growth_pct"])
    if "ddm" in v:
        L.append("    dividend model: %.0f per share" % v["ddm"])
    rel = v["relative"]
    for k, lab in (("ev_ebitda", "EV/EBITDA"), ("industry_ev_ebitda", "industry EV/EBITDA"), ("peg", "PEG (P/E over EPS growth; about 1 or below is moderate)"),
                   ("own_pe_percentile", "today's P/E sits at this percentile of the stock's own history")):
        if k in rel:
            L.append("    %s: %s" % (lab, rel[k]))
    for n in v["notes"]:
        L.append("    ~ " + n)
    return "\n".join(L)


# ---------------------------------------------------------------- thesis tracking (decouple from noise)

OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}


def flatten(facts):
    flat = {}
    for sec in ("quality", "growth", "valuation", "ownership", "returns"):
        for k, v in (facts.get(sec) or {}).items():
            if f(v) is not None:
                flat[k] = f(v)
    b = facts.get("balance") or {}
    if f(b.get("equity")) and f(b.get("debt")) is not None:
        flat["debt_to_equity"] = f(b["debt"]) / f(b["equity"])
    for k, v in (facts.get("red_flags") or {}).items():
        flat[k] = 1.0 if v else 0.0
    return flat


def parse_break(text):
    import re
    m = re.match(r"^\s*([a-z_0-9]+)\s*(<=|>=|==|<|>)\s*(-?[0-9.]+)\s*$", text)
    if not m:
        raise AnalysisError('a break condition looks like "roe_pct<12" or "debt_to_equity>1.0" or "pledged==1", got %r' % text)
    return {"metric": m.group(1), "op": m.group(2), "value": float(m.group(3))}


def review_thesis(thesis, facts):
    flat = flatten(facts)
    broken, unknown, held = [], [], []
    for c in thesis["breaks"]:
        v = flat.get(c["metric"])
        label = "%s %s %g" % (c["metric"], c["op"], c["value"])
        if v is None:
            unknown.append(label)
        elif OPS[c["op"]](v, c["value"]):
            broken.append("%s (now %.2f)" % (label, v))
        else:
            held.append("%s not triggered (now %.2f)" % (label, v))
    status = "BROKEN" if broken else "UNKNOWN" if unknown and not held else "INTACT"
    return {"status": status, "broken": broken, "unknown": unknown, "held": held}


# ---------------------------------------------------------------- portfolio, rebalancing, tax drag

TAX = {"stcg_pct": 20.0, "ltcg_pct": 12.5, "ltcg_exempt": 125000.0, "long_days": 365,
       "source": "listed equity, FY 2026-27 as reported by a secondary source (bigul.co); surcharge, cess and loss carry-forward rules are NOT modelled: confirm with a tax advisor or incometax.gov.in"}


def tax_on_sales(sales, today):
    """sales: [{"symbol","qty","avg_price","price","buy_date"}] -> dict with gains, tax estimate and tips."""
    st = lt = 0.0
    unknown, tips = [], []
    for s in sales:
        gain = (s["price"] - s["avg_price"]) * s["qty"]
        bd = s.get("buy_date")
        if not bd:
            unknown.append(s["symbol"])
            continue
        days = (today - datetime.date.fromisoformat(str(bd)[:10])).days
        if days > TAX["long_days"]:
            lt += gain
        else:
            st += gain
            left = TAX["long_days"] + 1 - days
            if gain > 0 and left <= 45:
                saving = gain * (TAX["stcg_pct"] - TAX["ltcg_pct"]) / 100.0
                tips.append("%s: waiting %d more day(s) makes this gain long-term (about Rs %.0f less tax)" % (s["symbol"], left, saving))
    if st < 0:
        lt += st
        st = 0.0
    taxable_lt = max(lt - TAX["ltcg_exempt"], 0.0)
    return {"stcg": round(st, 2), "ltcg": round(lt, 2), "tax": round(TAX["stcg_pct"] / 100.0 * max(st, 0) + TAX["ltcg_pct"] / 100.0 * taxable_lt, 2),
            "unknown": unknown, "tips": tips}


def portfolio(holdings, targets=None, max_position=15.0, max_sector=35.0, new_cash=0.0, allow_sells=False, today=None):
    today = today or datetime.date.today()
    rows = []
    for h in holdings:
        qty, last, avg = f(h.get("qty", h.get("quantity"))), f(h.get("last_price", h.get("price"))), f(h.get("avg_price", h.get("average_price")))
        if not qty or not last:
            continue
        rows.append({"symbol": ta.clean_symbol(h.get("symbol") or h.get("tradingsymbol")), "qty": qty, "price": last, "avg_price": avg or last,
                     "value": qty * last, "sector": h.get("sector") or "Unknown", "buy_date": h.get("buy_date")})
    total = sum(r["value"] for r in rows)
    if not total:
        raise AnalysisError("no holdings with a quantity and a price")
    for r in rows:
        r["weight"] = r["value"] / total * 100.0
    sectors = {}
    for r in rows:
        sectors[r["sector"]] = sectors.get(r["sector"], 0.0) + r["weight"]
    flags = ["%s is %.1f%% of the portfolio (limit %.0f%%)" % (r["symbol"], r["weight"], max_position) for r in rows if r["weight"] > max_position]
    flags += ["sector %s is %.1f%% (limit %.0f%%)" % (k, v, max_sector) for k, v in sectors.items() if v > max_sector and k != "Unknown"]
    if sectors.get("Unknown", 0) > 30:
        flags.append("%.0f%% of the portfolio has no sector set: concentration may be hidden" % sectors["Unknown"])
    plan, sells = [], []
    if targets:
        pool = total + new_cash
        tw = {ta.clean_symbol(k): float(v) for k, v in targets.items()}
        cur = {r["symbol"]: r for r in rows}
        cash_left = new_cash
        diffs = []
        for sym, w in tw.items():
            have = cur[sym]["value"] if sym in cur else 0.0
            diffs.append((sym, w / 100.0 * pool - have, cur.get(sym)))
        for sym, d, row in sorted(diffs, key=lambda x: -x[1]):
            if d > 0 and cash_left > 0:
                use = min(d, cash_left)
                cash_left -= use
                plan.append(("BUY", sym, use, "from new money"))
            elif d < 0 and allow_sells and row:
                q = int(min(row["qty"], (-d) // row["price"]))
                if q > 0:
                    plan.append(("SELL", sym, q * row["price"], "to reach target"))
                    sells.append({"symbol": sym, "qty": q, "avg_price": row["avg_price"], "price": row["price"], "buy_date": row["buy_date"]})
            elif d < 0:
                plan.append(("OVER", sym, -d, "above target: let new money dilute it instead of selling (no tax)"))
    tax = tax_on_sales(sells, today) if sells else None
    return {"total": round(total, 2), "rows": sorted(rows, key=lambda r: -r["value"]), "sectors": sectors, "flags": flags, "plan": plan, "tax": tax}


def render_portfolio(p):
    L = ["PORTFOLIO  total Rs %.0f" % p["total"]]
    for r in p["rows"]:
        gain = (r["price"] - r["avg_price"]) / r["avg_price"] * 100.0
        L.append("    %-12s %6.1f%%  Rs %-12.0f %-18s %+.1f%% vs cost" % (r["symbol"], r["weight"], r["value"], r["sector"], gain))
    L.append("    sectors: " + ", ".join("%s %.0f%%" % (k, v) for k, v in sorted(p["sectors"].items(), key=lambda kv: -kv[1])))
    for fl in p["flags"]:
        L.append("    ! " + fl)
    if p["plan"]:
        L.append("    REBALANCE (new money first, sells only if asked):")
        for act, sym, amt, why in p["plan"]:
            L.append("      %-5s %-12s Rs %-10.0f %s" % (act, sym, amt, why))
    if p["tax"]:
        t = p["tax"]
        L.append("    tax drag on the planned sells: short-term gain %.0f, long-term gain %.0f, estimated tax Rs %.0f" % (t["stcg"], t["ltcg"], t["tax"]))
        for u in t["unknown"]:
            L.append("      %s: add buy_date to estimate its tax" % u)
        for tip in t["tips"]:
            L.append("      tip: " + tip)
        L.append("      " + TAX["source"])
    L.append("    Fewer, larger, rarely-changed positions beat frequent trading: each sale can trigger tax.")
    return "\n".join(L)


def cmd_read(a):
    facts = json.loads(ta.read_text(a.facts))
    if not isinstance(facts, dict) or not facts.get("symbol"):
        raise AnalysisError('the facts need a "symbol"')
    sym = ta.clean_symbol(facts["symbol"])
    bars = ta.load_bars(a.bars)
    fund, tech = judge(facts), entry_view(bars)
    verdict, line = combine(fund, tech)
    today = ta.india_now(a.now).date().isoformat()
    rec = {"symbol": sym, "date": today, "horizon": "longterm", "verdict": verdict, "summary": line,
           "fundamentals": fund, "entry": tech, "facts": facts}
    doc = ta.load_json(a.out, {"stocks": {}})
    doc.setdefault("stocks", {})[sym] = rec
    ta.save_json(a.out, doc)
    print("%s  %s" % (verdict, sym))
    print("    " + line)
    print("\nFUNDAMENTALS: %s" % fund["stance"])
    for r in fund["reasons"]:
        print("    - " + r)
    for k in ORDER:
        p = fund["pillars"][k]
        print("    %-14s %-8s %s" % (k, p["rating"], "; ".join(p["why"])))
    for n in fund["notes"]:
        print("    ~ " + n)
    print("\n" + render_entry(tech))
    if facts.get("valuation_inputs"):
        print("\n" + render_value(value_model(facts)))
    print("\n" + IGNORED)
    print("A research read, not advice and not a price target.")
    return 0


def cmd_zones(a):
    print(render_entry(entry_view(ta.load_bars(a.bars))))
    return 0



def cmd_value(a):
    facts = json.loads(ta.read_text(a.facts))
    print(render_value(value_model(facts)))
    return 0


def cmd_portfolio(a):
    holdings = json.loads(ta.read_text(a.holdings))
    if isinstance(holdings, dict):
        holdings = holdings.get("holdings") or holdings.get("data") or []
    targets = json.loads(ta.read_text(a.targets)) if a.targets else None
    today = ta.india_now(a.now).date()
    p = portfolio(holdings, targets, a.max_position, a.max_sector, a.new_cash, a.allow_sells, today)
    print(render_portfolio(p))
    return 0


def cmd_thesis(a):
    doc = ta.load_json(a.out, {})
    sym = ta.clean_symbol(a.symbol)
    if a.action == "add":
        if not a.text or not a.brk:
            raise AnalysisError('give --text "why you own it" and at least one --break "metric<value"')
        doc[sym] = {"text": a.text, "entry_price": a.entry_price, "date": ta.india_now(a.now).date().isoformat(),
                    "breaks": [parse_break(b) for b in a.brk]}
        ta.save_json(a.out, doc)
        print("saved the thesis for %s with %d break condition(s). Price moves are not on the list on purpose." % (sym, len(a.brk)))
        return 0
    t = doc.get(sym)
    if not t:
        print("No thesis saved for %s. Write one before buying: what must stay true for you to hold?" % sym)
        return 1
    if a.action == "show":
        print("%s (since %s, entry %s)\n  why: %s" % (sym, t["date"], t.get("entry_price"), t["text"]))
        for c in t["breaks"]:
            print("  breaks if %s %s %g" % (c["metric"], c["op"], c["value"]))
        return 0
    facts = json.loads(ta.read_text(a.facts))
    r = review_thesis(t, facts)
    print("THESIS %s: %s" % (r["status"], sym))
    print("  why you own it: " + t["text"])
    for x in r["broken"]:
        print("  BROKEN: " + x)
    for x in r["held"]:
        print("  ok: " + x)
    for x in r["unknown"]:
        print("  not in the facts: " + x)
    print("  A lower price alone is not a broken thesis. Decide on these conditions, not on the news or the chart.")
    return 0 if r["status"] != "BROKEN" else 2


def cmd_rules(a):
    print(json.dumps({"fundamentals": RULES, "entry": TECH}, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Long-term investing read (trader-longterm).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("read"); p.add_argument("facts"); p.add_argument("bars")
    p.add_argument("--out", default="trader/longterm.json"); p.add_argument("--now")
    p = sub.add_parser("zones"); p.add_argument("bars")
    p = sub.add_parser("value"); p.add_argument("facts")
    p = sub.add_parser("portfolio"); p.add_argument("holdings"); p.add_argument("--targets")
    p.add_argument("--max-position", type=float, default=15.0); p.add_argument("--max-sector", type=float, default=35.0)
    p.add_argument("--new-cash", type=float, default=0.0); p.add_argument("--allow-sells", action="store_true"); p.add_argument("--now")
    p = sub.add_parser("thesis"); p.add_argument("action", choices=["add", "review", "show"]); p.add_argument("symbol")
    p.add_argument("facts", nargs="?"); p.add_argument("--text"); p.add_argument("--entry-price", type=float)
    p.add_argument("--break", dest="brk", action="append"); p.add_argument("--out", default="trader/thesis.json"); p.add_argument("--now")
    sub.add_parser("rules")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"read": cmd_read, "zones": cmd_zones, "value": cmd_value, "portfolio": cmd_portfolio,
                "thesis": cmd_thesis, "rules": cmd_rules}[a.cmd](a)
    except (ta.TaError, ValueError, KeyError, OSError) as e:
        print("longterm.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
