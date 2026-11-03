#!/usr/bin/env python3
"""Swing-trade levels for trader-swing (hold days to weeks). Standard library only, no network.

Technicals decide the entry, the stop and the targets: the daily trend, support and resistance
zones from daily pivots, the next session's floor-pivot levels, and a volume check on breakouts.
Short-term headwinds (a results date, a weak market) only SHRINK the plan: smaller size or a
higher reward:risk bar. Weak fundamentals are a note, not a veto, except surveillance/default
red flags, which stop the plan.

    python3 swing.py scan bars.json [--symbol X] [--index nifty.json] [--facts facts.json]
                                    [--max-loss 2000] [--budget 100000] [--tick 0.05] [--now ...]
    python3 swing.py rules

bars.json = DAILY OHLC (6+ months), Kite historical data (day) or CSV. Optional facts.json:
  {"symbol": "SWIGGY", "fundamentals": "WATCH",              # CANDIDATE | WATCH | AVOID, from a fundamentals read
   "red_flags": {"asm": false, "gsm": false, "default_risk": false},
   "events": [{"kind": "results", "date": "2026-11-05", "note": "Q2"}]}

Writes trader/swing.json. It places no orders: trader-broker is built for same-day trades, so a swing
entry and its stop (a GTT or a stop-loss order) are placed by hand in Kite. Education and tooling.
"""
import argparse
import json
import datetime
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ta  # noqa: E402

f = ta.f

RULES = {
    "zone_tol_pct": 1.5, "pivot_k": 3, "lookback_bars": 250, "min_touches": 2,
    "stop_atr": 0.5,              # stop sits this many ATRs beyond the zone
    "min_rr": 1.5, "min_rr_weak_market": 2.0, "counter_trend_min_rr": 2.0,
    "breakout_volume_x": 1.3, "rsi_max_for_bounce": 60.0,
    "event_days": 10, "weak_market_size": 0.5, "counter_trend_size": 0.5, "max_position_pct": 25.0,
}


def tick_round(x, tick, mode="nearest"):
    q = x / tick
    n = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else math.floor(q + 0.5)
    return round(n * tick, 2)


def trend_of(bars):
    cl = [b.c for b in bars]
    e20, e50 = ta.ema(cl, 20), ta.ema(cl, 50)
    if e50[-1] is None or len(bars) < 61:
        return "unknown", e20[-1], e50[-1]
    rising = e50[-1] > e50[-11]
    if cl[-1] > e20[-1] > e50[-1] and rising:
        return "up", e20[-1], e50[-1]
    if cl[-1] < e20[-1] < e50[-1] and not rising:
        return "down", e20[-1], e50[-1]
    return "sideways", e20[-1], e50[-1]


def analyse(bars, facts=None, index_bars=None, max_loss=None, budget=None, tick=0.05, today=None):
    """Pure function -> dict with the trend, levels, the best setup (or why none) and the sizing."""
    r = RULES
    facts = facts or {}
    if len(bars) < 80:
        raise ta.TaError("need at least 80 daily bars (about 4 months); 6+ months is better")
    cl = [b.c for b in bars]
    last = bars[-1]
    a = ta.last(ta.atr(bars, 14))
    rsi14 = ta.last(ta.rsi(cl, 14))
    vols = [b.v for b in bars[-21:-1] if b.v]
    avgv = sum(vols) / len(vols) if vols else None
    trend, e20, e50 = trend_of(bars)
    sup, res = ta.support_resistance(bars, r["pivot_k"], r["zone_tol_pct"], r["lookback_bars"])
    levels = ta.floor_pivots(last)
    out = {"last": last.c, "date": last.d, "trend": trend, "ema20": round(e20, 2) if e20 else None,
           "ema50": round(e50, 2) if e50 else None, "atr": round(a, 2), "rsi14": round(rsi14, 1) if rsi14 else None,
           "next_session_levels": {k: round(v, 2) for k, v in levels.items()},
           "support": [{"low": round(z["low"], 2), "high": round(z["high"], 2), "touches": z["touches"]}
                       for z in sorted([z for z in sup if z["low"] < last.c], key=lambda z: -z["high"])[:4]],
           "resistance": [{"low": round(z["low"], 2), "high": round(z["high"], 2), "touches": z["touches"]}
                          for z in sorted([z for z in res if z["high"] > last.c], key=lambda z: z["low"])[:4]]}
    notes, size_factor, min_rr = [], 1.0, r["min_rr"]

    flags = facts.get("red_flags") or {}
    bad = [k for k in ("asm", "gsm", "default_risk") if flags.get(k)]
    if bad:
        out.update(setup=None, reason="surveillance/default red flag (%s): no swing plan" % ", ".join(bad), notes=notes)
        return out
    fund = str(facts.get("fundamentals") or "").upper()
    if fund in ("WATCH", "AVOID"):
        notes.append("fundamentals are %s: for a swing trade this is a note, not a veto, but keep the stop strict and do not average down" % fund)
    if index_bars and len(index_bars) >= 61:
        it, _, _ = trend_of(index_bars)
        out["index_trend"] = it
        if it == "down":
            size_factor *= r["weak_market_size"]
            min_rr = max(min_rr, r["min_rr_weak_market"])
            notes.append("the index trend is down: half size and a %.1f:1 minimum reward:risk" % min_rr)
    today = today or datetime.date.fromisoformat(last.d)
    for e in facts.get("events") or []:
        try:
            d = datetime.date.fromisoformat(str(e.get("date"))[:10])
        except ValueError:
            continue
        if 0 <= (d - today).days <= r["event_days"]:
            notes.append("%s on %s (%s): a gap can jump the stop; exit before it or take half size" % (e.get("kind", "event"), d.isoformat(), e.get("note", "")))
            size_factor = min(size_factor, 0.5)

    conf = confirmations(bars)
    pats = chart_patterns(bars)
    out["confirmations"] = conf
    out["patterns"] = pats
    notes.extend(confirmation_notes(conf))
    for p in pats:
        notes.append("%s pattern (%s, %s): %s" % (p["name"], p["bias"], p["status"], p["note"]))
    cands = []
    rng = max(last.h - last.l, 1e-9)
    # bounce off a support zone that has held
    for z in sup:
        if z["touches"] < r["min_touches"]:
            continue
        near = last.l <= z["high"] + 0.5 * a and last.c >= z["low"]
        confirm = last.c > last.o or (last.c - last.l) / rng >= 0.5
        if near and confirm and (rsi14 is None or rsi14 <= r["rsi_max_for_bounce"]):
            entry = tick_round(last.c, tick)
            stop = tick_round(z["low"] - r["stop_atr"] * a, tick, "down")
            ups = sorted([x for x in res if x["low"] > entry * 1.005], key=lambda x: x["low"])
            if stop < entry and ups:
                cands.append(("Bounce off support %.2f to %.2f (held %d times)" % (z["low"], z["high"], z["touches"]),
                              entry, stop, [tick_round(x["low"], tick) for x in ups[:2]], trend == "down"))
    # breakout above resistance on volume
    for z in res:
        if z["touches"] < r["min_touches"]:
            continue
        crossed = last.c > z["high"] and min(b.c for b in bars[-4:-1]) <= z["high"] * 1.005
        vol_ok = avgv is None or last.v >= r["breakout_volume_x"] * avgv
        if crossed and vol_ok:
            entry = tick_round(last.c, tick)
            stop = tick_round(z["low"] - 0.25 * a, tick, "down")
            if stop < entry:
                ups = sorted([x for x in res if x["low"] > entry * 1.005], key=lambda x: x["low"])
                tg = [tick_round(x["low"], tick) for x in ups[:2]] or [tick_round(entry + 2 * (entry - stop), tick, "up")]
                cands.append(("Breakout above resistance %.2f to %.2f on volume" % (z["low"], z["high"]), entry, stop, tg, False))

    best = None
    for name, entry, stop, tg, counter in cands:
        risk = entry - stop
        rr = (tg[0] - entry) / risk if risk > 0 else 0
        need = max(min_rr, r["counter_trend_min_rr"]) if counter else min_rr
        if rr >= need and (best is None or rr > best["rr"]):
            best = {"setup": name, "entry": entry, "stop": stop, "targets": tg, "rr": round(rr, 2),
                    "risk_per_share": round(risk, 2), "counter_trend": counter, "needs_rr": need}
    if not best:
        why = "no setup: " + ("; ".join("%s (reward:risk %.1f, needs %.1f)" % (n, (t[0] - e) / (e - s) if e > s else 0, max(min_rr, 2.0) if c else min_rr)
                                         for n, e, s, t, c in cands) if cands else
                              "price is not at a held support zone with a bullish bar, and has not just broken out on volume")
        out.update(setup=None, reason=why, notes=notes)
        return out
    if best["counter_trend"]:
        size_factor = min(size_factor, r["counter_trend_size"])
        notes.append("counter-trend (daily trend is down): half size, first target only")
    qty = None
    if max_loss:
        qty = int((max_loss * size_factor) // best["risk_per_share"])
        if budget:
            qty = min(qty, int((budget * r["max_position_pct"] / 100.0) // best["entry"]))
    best.update(size_factor=size_factor, qty=qty,
                max_loss=round(qty * best["risk_per_share"], 2) if qty else None,
                position=round(qty * best["entry"], 2) if qty else None)
    best["invalid_if"] = "a daily close below %.2f (the stop); after the first target is hit, move the stop to the entry" % best["stop"]
    out.update(best, notes=notes)
    return out



# ---------------------------------------------------------------- chart patterns (pivot based, mirrored for bearish)

def mirror(bars):
    """Flip prices upside down so a bullish detector finds the bearish twin."""
    return [ta.Bar(b.d, -b.o, -b.l, -b.h, -b.c, b.v) for b in bars]


def _bull_patterns(bars, k=3, tol=3.0):
    """Bullish structures in the last ~120 bars. Each: {name, status, level, note}."""
    bars = bars[-120:]
    hi, lo = ta.pivots(bars, k)
    last = bars[-1].c
    out = []
    if len(lo) >= 2:
        i1, i2 = lo[-2], lo[-1]
        l1, l2 = bars[i1].l, bars[i2].l
        mids = [bars[j].h for j in hi if i1 < j < i2]
        if i2 - i1 >= 8 and abs(l1 - l2) / l1 * 100 <= tol and mids:
            neck = max(mids)
            out.append({"name": "Double bottom", "status": "confirmed" if last > neck else "forming", "level": round(neck, 2),
                        "note": "two lows near %.2f; a close above the neckline %.2f completes it" % ((l1 + l2) / 2, neck)})
    # inverse head and shoulders: three lows, the middle one the lowest
    if len(lo) >= 3:
        a, b, c = lo[-3:]
        la, lb, lc = bars[a].l, bars[b].l, bars[c].l
        necks = [bars[j].h for j in hi if a < j < c]
        if lb < la and lb < lc and abs(la - lc) / la * 100 <= 5.0 and len(necks) >= 1 and c - a >= 14:
            neck = max(necks)
            out.append({"name": "Inverse head and shoulders", "status": "confirmed" if last > neck else "forming", "level": round(neck, 2),
                        "note": "head %.2f between shoulders %.2f and %.2f; neckline near %.2f" % (lb, la, lc, neck)})
    # ascending triangle: flat highs, rising lows
    if len(hi) >= 3 and len(lo) >= 3:
        h3, l3 = [bars[j].h for j in hi[-3:]], [bars[j].l for j in lo[-3:]]
        flat = (max(h3) - min(h3)) / max(h3) * 100 <= 1.5
        rising = l3[0] < l3[1] < l3[2]
        if flat and rising:
            top = max(h3)
            out.append({"name": "Ascending triangle", "status": "confirmed" if last > top else "forming", "level": round(top, 2),
                        "note": "flat resistance near %.2f with higher lows; a close above it with volume is the trigger" % top})
    # bull flag: sharp pole then a shallow, quiet drift
    n = len(bars)
    if n >= 25:
        for pole_len in (5, 7, 10):
            for flag_len in (5, 8, 12):
                p0, p1 = n - 1 - flag_len - pole_len, n - 1 - flag_len
                if p0 < 0:
                    continue
                pole = (bars[p1].c - bars[p0].c) / bars[p0].c * 100
                fl = bars[p1 + 1:]
                if not fl:
                    continue
                depth = (max(b.h for b in fl) - min(b.l for b in fl))
                pole_pts = bars[p1].c - bars[p0].c
                if pole >= 8.0 and 0 < depth <= 0.5 * pole_pts and min(b.l for b in fl) > bars[p1].c - 0.5 * pole_pts:
                    top = max(b.h for b in fl)
                    out.append({"name": "Bull flag", "status": "confirmed" if last > top else "forming", "level": round(top, 2),
                                "note": "a %.0f%% pole then a shallow pause; a close above %.2f resumes it" % (pole, top)})
                    return out
    return out


def chart_patterns(bars):
    """-> list of {name, bias, status, level, note}. Bullish ones from the bars, bearish ones from the mirrored bars."""
    res = []
    for p in _bull_patterns(bars):
        p["bias"] = "bullish"
        res.append(p)
    for p in _bull_patterns(mirror(bars)):
        nm = {"Double bottom": "Double top", "Inverse head and shoulders": "Head and shoulders",
              "Ascending triangle": "Descending triangle", "Bull flag": "Bear flag"}[p["name"]]
        lvl = -p["level"]
        note = (p["note"].replace("lows", "highs").replace("above", "below").replace("higher lows", "lower highs")
                .replace("resistance", "support").replace("neckline", "neckline"))
        res.append({"name": nm, "bias": "bearish", "status": p["status"], "level": round(lvl, 2), "note": note})
    return res


# ---------------------------------------------------------------- confirmation: MACD, Bollinger, volume, candles

def _candle_bars(bars):
    try:
        import candles
    except ImportError:
        return None, None
    return candles, [candles.Bar(b.d, b.o, b.h, b.l, b.c, b.v) for b in bars]


def confirmations(bars):
    """Momentum, squeeze, volume pressure and the candles of the last two bars."""
    cl = [b.c for b in bars]
    out = {"candles": []}
    line, sig, hist = ta.macd(cl)
    if hist[-1] is not None and hist[-2] is not None:
        out["macd"] = "bullish" if line[-1] > sig[-1] else "bearish"
        out["macd_hist_rising"] = hist[-1] > hist[-2]
        out["macd_cross"] = ("up" if line[-2] <= sig[-2] and line[-1] > sig[-1] else
                             "down" if line[-2] >= sig[-2] and line[-1] < sig[-1] else None)
    mid, up, lo, bw = ta.bollinger(cl)
    if bw[-1] is not None:
        series = [x for x in bw if x is not None]
        out["bandwidth_pct"] = round(bw[-1], 2)
        out["squeeze"] = len(series) >= 60 and ta.percentile_rank(bw[-1], series[-120:]) <= 20
        out["position_in_bands"] = round((cl[-1] - lo[-1]) / (up[-1] - lo[-1]), 2) if up[-1] > lo[-1] else None
    vp = ta.volume_pressure(bars)
    out["volume_pressure"] = round(vp, 2) if vp is not None else None
    cm, cb = _candle_bars(bars)
    if cm:
        out["candles"] = [{"date": h["date"], "pattern": h["pattern"], "bias": h["bias"]}
                          for h in cm.scan(cb, start=len(cb) - 2)]
    return out


def confirmation_notes(c, setup_bullish=True):
    n = []
    if c.get("macd") == "bearish" and setup_bullish:
        n.append("MACD is below its signal line: momentum does not back the long yet")
    if c.get("macd_cross") == "up":
        n.append("MACD just crossed up: a momentum confirmation")
    if c.get("squeeze"):
        n.append("Bollinger bands are in a squeeze (narrow): a bigger move is likely, direction still to be shown by the breakout")
    if c.get("volume_pressure") is not None:
        n.append("volume on up days is %.1f times volume on down days (20 bars)" % c["volume_pressure"] if c["volume_pressure"] >= 1 else
                 "volume on down days is heavier than on up days (ratio %.1f): sellers are more active" % c["volume_pressure"])
    for h in c.get("candles", []):
        n.append("%s candle on %s (%s)" % (h["pattern"], h["date"], h["bias"]))
    return n


# ---------------------------------------------------------------- screener

def screen_one(bars, index_bars=None, min_turnover=5e7):
    if len(bars) < 80:
        return None
    cl = [b.c for b in bars]
    turn = [b.c * b.v for b in bars[-20:] if b.v]
    avg_turn = sum(turn) / len(turn) if turn else 0.0
    ret20 = (cl[-1] / cl[-21] - 1) * 100
    rs = None
    if index_bars and len(index_bars) > 21:
        rs = ret20 - (index_bars[-1].c / index_bars[-21].c - 1) * 100
    a14, a50 = ta.last(ta.atr(bars, 14)), ta.last(ta.atr(bars, 50))
    contraction = (a14 / a50) if a14 and a50 else None
    _, _, _, bw = ta.bollinger(cl)
    series = [x for x in bw if x is not None]
    tight = ta.percentile_rank(bw[-1], series[-120:]) if bw[-1] is not None and len(series) >= 30 else None
    trend, _, _ = trend_of(bars)
    score, why = 0, []
    if avg_turn < min_turnover:
        return {"liquid": False, "avg_turnover": avg_turn, "score": -1, "why": ["average daily turnover below the liquidity floor"]}
    if trend == "up":
        score += 2; why.append("uptrend")
    if rs is not None and rs > 0:
        score += 2; why.append("beating the index by %.1f points over 20 days" % rs)
    if contraction is not None and contraction < 0.85:
        score += 1; why.append("volatility contracting (ATR14/ATR50 %.2f)" % contraction)
    if tight is not None and tight <= 25:
        score += 1; why.append("tight Bollinger bands")
    return {"liquid": True, "avg_turnover": avg_turn, "trend": trend, "rs_20d": rs, "contraction": contraction, "score": score, "why": why}


def cmd_screen(a):
    import glob
    idx = ta.load_bars(a.index) if a.index else None
    rows = []
    for p in sorted(glob.glob(os.path.join(a.dir, "*.csv")) + glob.glob(os.path.join(a.dir, "*.json"))):
        try:
            r = screen_one(ta.load_bars(p), idx, a.min_turnover)
        except (ta.TaError, ValueError, KeyError, OSError):
            continue
        if r:
            r["symbol"] = ta.clean_symbol(os.path.splitext(os.path.basename(p))[0])
            rows.append(r)
    rows.sort(key=lambda r: -r["score"])
    print("SCREEN: liquid stocks first, best score first (a short list to chart, not a list to buy)")
    for r in rows[:a.top]:
        if not r["liquid"]:
            continue
        print("    %-12s score %d  %s" % (r["symbol"], r["score"], "; ".join(r["why"]) or "nothing stands out"))
    skipped = [r["symbol"] for r in rows if not r["liquid"]]
    if skipped:
        print("    skipped as illiquid: " + ", ".join(skipped))
    return 0


# ---------------------------------------------------------------- trade tracking (stop discipline)

def check_trade(t, bars):
    """-> (status, message). Uses closes for the stop (as the plan says) and highs for targets."""
    last = bars[-1]
    if last.c <= t["stop"]:
        return "STOP", "closed at %.2f, below the stop %.2f: exit now. Do not turn this into an investment or move the stop down." % (last.c, t["stop"])
    days = (datetime.date.fromisoformat(last.d) - datetime.date.fromisoformat(t["date"])).days
    for i, tg in enumerate(t["targets"], 1):
        if last.h >= tg and not t.get("hit", 0) >= i:
            return "TARGET", "target %d (%.2f) was reached: take profit on part and move the stop to the entry %.2f" % (i, tg, t["entry"])
    if days >= t.get("max_days", 15):
        return "TIME", "%d days and no result: the trade is stale, exit and free the capital" % days
    r_now = (last.c - t["entry"]) / (t["entry"] - t["stop"]) if t["entry"] > t["stop"] else 0
    return "HOLD", "inside the plan, %+.1f R after %d days (stop %.2f)" % (r_now, days, t["stop"])


def cmd_track(a):
    doc = ta.load_json(a.out, {"open": {}, "closed": []})
    sym = ta.clean_symbol(a.symbol) if a.symbol else None
    if a.action == "add":
        if not (sym and a.entry and a.stop and a.target):
            raise ta.TaError("add needs SYMBOL --entry --stop --target (repeat --target for more)")
        if a.stop >= a.entry:
            raise ta.TaError("a long swing stop must be below the entry")
        doc["open"][sym] = {"entry": a.entry, "stop": a.stop, "targets": sorted(a.target), "qty": a.qty,
                            "date": ta.india_now(a.now).date().isoformat(), "max_days": a.max_days, "hit": 0}
        ta.save_json(a.out, doc)
        print("tracking %s: entry %.2f, stop %.2f, targets %s, time stop %d days" % (sym, a.entry, a.stop, doc["open"][sym]["targets"], a.max_days))
        return 0
    if a.action == "check":
        worst = 0
        for s, t in sorted(doc["open"].items()):
            if not a.bars_dir:
                raise ta.TaError("check needs --bars-dir with one SYMBOL.csv per open trade")
            p = os.path.join(a.bars_dir, s + ".csv")
            if not os.path.exists(p):
                p = os.path.join(a.bars_dir, s + ".json")
            st, msg = check_trade(t, ta.load_bars(p))
            print("%-12s %-6s %s" % (s, st, msg))
            worst = max(worst, 2 if st in ("STOP", "TIME") else 0)
        if not doc["open"]:
            print("no open swing trades")
        return worst
    if a.action == "close":
        t = doc["open"].pop(sym, None)
        if not t or not a.exit_price:
            raise ta.TaError("close needs an open SYMBOL and --exit-price")
        risk = t["entry"] - t["stop"]
        res = {"symbol": sym, "entry": t["entry"], "exit": a.exit_price, "r": round((a.exit_price - t["entry"]) / risk, 2), "date": ta.india_now(a.now).date().isoformat()}
        doc["closed"].append(res)
        ta.save_json(a.out, doc)
        print("closed %s at %.2f: %+.2f R" % (sym, a.exit_price, res["r"]))
        return 0
    for s, t in doc["open"].items():
        print("%-12s entry %.2f stop %.2f targets %s since %s" % (s, t["entry"], t["stop"], t["targets"], t["date"]))
    return 0


def render(sym, o):
    L = ["%s  %s  trend %s | ATR %s | RSI %s | last %.2f (%s)" % (sym, "PLAN" if o.get("setup") else "NO PLAN", o["trend"], o["atr"], o["rsi14"], o["last"], o["date"])]
    if o.get("setup"):
        L.append("    %s" % o["setup"])
        L.append("    entry %.2f | stop %.2f | targets %s | reward:risk %.2f (needs %.1f) | risk/share %.2f"
                 % (o["entry"], o["stop"], ", ".join("%.2f" % t for t in o["targets"]), o["rr"], o["needs_rr"], o["risk_per_share"]))
        if o.get("qty") is not None:
            L.append("    size: %d shares = %.2f at risk, position %.2f (size factor %.2f)" % (o["qty"], o["max_loss"] or 0, o["position"] or 0, o["size_factor"]))
        else:
            L.append("    give --max-loss to size it")
        L.append("    invalid if " + o["invalid_if"])
    else:
        L.append("    " + o["reason"])
    for s in o["support"]:
        L.append("    support    %.2f to %.2f (touches %d)" % (s["low"], s["high"], s["touches"]))
    for s in o["resistance"]:
        L.append("    resistance %.2f to %.2f (touches %d)" % (s["low"], s["high"], s["touches"]))
    p = o["next_session_levels"]
    L.append("    next session: S2 %.2f | S1 %.2f | P %.2f | R1 %.2f | R2 %.2f" % (p["S2"], p["S1"], p["P"], p["R1"], p["R2"]))
    for n in o["notes"]:
        L.append("    ~ " + n)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Swing-trade levels (trader-swing).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan"); p.add_argument("bars")
    p.add_argument("--symbol"); p.add_argument("--index"); p.add_argument("--facts")
    p.add_argument("--max-loss", type=float); p.add_argument("--budget", type=float)
    p.add_argument("--tick", type=float, default=0.05); p.add_argument("--out", default="trader/swing.json")
    p.add_argument("--now")
    p = sub.add_parser("screen"); p.add_argument("dir"); p.add_argument("--index"); p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-turnover", type=float, default=5e7)
    p = sub.add_parser("track"); p.add_argument("action", choices=["add", "check", "close", "list"]); p.add_argument("symbol", nargs="?")
    p.add_argument("--entry", type=float); p.add_argument("--stop", type=float); p.add_argument("--target", type=float, action="append")
    p.add_argument("--qty", type=int); p.add_argument("--max-days", type=int, default=15); p.add_argument("--exit-price", type=float)
    p.add_argument("--bars-dir"); p.add_argument("--out", default="trader/swing_trades.json"); p.add_argument("--now")
    sub.add_parser("rules")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        if a.cmd == "rules":
            print(json.dumps(RULES, indent=2))
            return 0
        if a.cmd == "screen":
            return cmd_screen(a)
        if a.cmd == "track":
            return cmd_track(a)
        facts = json.loads(ta.read_text(a.facts)) if a.facts else {}
        sym = ta.clean_symbol(a.symbol or facts.get("symbol") or "")
        bars = ta.load_bars(a.bars)
        idx = ta.load_bars(a.index) if a.index else None
        o = analyse(bars, facts, idx, a.max_loss, a.budget, a.tick)
        o["symbol"] = sym
        o["date_run"] = ta.india_now(a.now).date().isoformat()
        doc = ta.load_json(a.out, {"stocks": {}})
        doc.setdefault("stocks", {})[sym] = o
        ta.save_json(a.out, doc)
        print(render(sym, o))
        print("\nTechnicals set the plan; headwinds only shrink it. Places no orders. Not financial advice.")
        return 0
    except (ta.TaError, ValueError, KeyError, OSError) as e:
        print("swing.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
