#!/usr/bin/env python3
"""Candlestick pattern finder for trader-candles. Standard library only, no network.

Reads OHLC bars (oldest first) from a CSV or JSON file and lists the classic patterns it finds,
with the trend before them, a bullish/bearish/neutral bias and a plain-words note.

    python3 candles.py scan bars.csv [--last 5] [--timeframe day]
    python3 candles.py stats bars.csv [--horizon 5]    # how each pattern fared in THIS file's history
    python3 candles.py list                            # the patterns and their rules

Input: CSV with a header (date,open,high,low,close[,volume]) or JSON: a list of such objects, or
Kite's {"candles": [[date,o,h,l,c,v], ...]} / a bare list of [date,o,h,l,c,v] rows.

A pattern is a hint, not a signal. Standalone candlestick patterns have weak and unstable
edge; use them with trend, a support/resistance level, volume and a stop. `stats` shows how
little or how much a pattern did on your own data. Education and tooling, not financial advice.
"""
import argparse
import csv
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

P = {
    "doji_body": 0.10,        # body <= 10% of range
    "long_body": 0.60,        # body >= 60% of range
    "small_body": 0.30,
    "marubozu_shadow": 0.05,  # each shadow <= 5% of range
    "long_shadow": 2.0,       # shadow >= 2x body
    "trend_bars": 5,          # bars used to judge the trend before a pattern
    "trend_pct": 1.0,         # move of at least this % over those bars counts as a trend
}


class Bar:
    __slots__ = ("d", "o", "h", "l", "c", "v")

    def __init__(self, d, o, h, l, c, v=0.0):
        self.d, self.o, self.h, self.l, self.c, self.v = d, o, h, l, c, v

    @property
    def rng(self): return self.h - self.l
    @property
    def body(self): return abs(self.c - self.o)
    @property
    def top(self): return max(self.o, self.c)
    @property
    def bot(self): return min(self.o, self.c)
    @property
    def up(self): return self.c > self.o
    @property
    def down(self): return self.c < self.o
    @property
    def upper(self): return self.h - self.top
    @property
    def lower(self): return self.bot - self.l
    @property
    def mid(self): return (self.o + self.c) / 2.0


def load(path):
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    rows = []
    t = text.lstrip()
    if t[:1] in "[{":
        data = json.loads(t)
        if isinstance(data, dict):
            data = data.get("candles") or data.get("data", {}).get("candles") or data.get("bars") or []
        for r in data:
            if isinstance(r, dict):
                low = {k.lower(): v for k, v in r.items()}
                rows.append((low.get("date") or low.get("timestamp") or low.get("ts") or "", low["open"],
                             low["high"], low["low"], low["close"], low.get("volume", 0)))
            else:
                rows.append(tuple(r) + (0,) * (6 - len(r)))
    else:
        rd = csv.DictReader(text.splitlines())
        for r in rd:
            low = {k.strip().lower(): v for k, v in r.items()}
            rows.append((low.get("date") or low.get("timestamp") or low.get("datetime") or "", low["open"],
                         low["high"], low["low"], low["close"], low.get("volume") or 0))
    bars = []
    for d, o, h, l, c, v in rows:
        try:
            b = Bar(str(d), float(o), float(h), float(l), float(c), float(v or 0))
        except (TypeError, ValueError):
            continue
        if b.h < max(b.o, b.c) - 1e-9 or b.l > min(b.o, b.c) + 1e-9 or b.h < b.l:
            raise SystemExit("bad bar on %s: high/low do not contain open/close" % d)
        bars.append(b)
    if len(bars) < 3:
        raise SystemExit("need at least 3 bars")
    return bars


def trend(bars, i):
    """'up' / 'down' / 'flat' from the closes before bar i."""
    n = P["trend_bars"]
    if i < n:
        return "flat"
    a, b = bars[i - n].c, bars[i - 1].c
    if a <= 0:
        return "flat"
    pct = (b - a) / a * 100.0
    return "up" if pct >= P["trend_pct"] else "down" if pct <= -P["trend_pct"] else "flat"


def is_doji(b): return b.rng > 0 and b.body <= P["doji_body"] * b.rng
def is_long(b): return b.rng > 0 and b.body >= P["long_body"] * b.rng
def is_small(b): return b.rng > 0 and b.body <= P["small_body"] * b.rng


# Each detector: (bars, i, trend) -> (name, bias, note) or None. Multi-bar ones look back from i.
def one_bar(bars, i, tr):
    b = bars[i]
    out = []
    if b.rng <= 0:
        return out
    if is_long(b) and b.upper <= P["marubozu_shadow"] * b.rng and b.lower <= P["marubozu_shadow"] * b.rng:
        out.append(("Bullish marubozu" if b.up else "Bearish marubozu", "bullish" if b.up else "bearish",
                    "full-body candle with almost no shadows: one side controlled the whole bar"))
    if is_doji(b):
        if b.lower >= 0.6 * b.rng and b.upper <= 0.1 * b.rng:
            out.append(("Dragonfly doji", "bullish" if tr == "down" else "neutral", "sellers pushed down and were fully rejected"))
        elif b.upper >= 0.6 * b.rng and b.lower <= 0.1 * b.rng:
            out.append(("Gravestone doji", "bearish" if tr == "up" else "neutral", "buyers pushed up and were fully rejected"))
        elif b.upper >= 0.35 * b.rng and b.lower >= 0.35 * b.rng:
            out.append(("Long-legged doji", "neutral", "open and close equal with long shadows both ways: strong indecision"))
        else:
            out.append(("Doji", "neutral", "open and close almost equal: indecision, needs the next bar"))
    elif b.body > 0:
        small_top = b.upper <= 0.15 * b.rng or b.upper < 0.5 * b.body
        small_bot = b.lower <= 0.15 * b.rng or b.lower < 0.5 * b.body
        if b.lower >= P["long_shadow"] * b.body and small_top and b.body <= 0.35 * b.rng:
            if tr == "down":
                out.append(("Hammer", "bullish", "long lower shadow after a fall: buyers stepped in; wait for a higher close"))
            elif tr == "up":
                out.append(("Hanging man", "bearish", "long lower shadow after a rise: warning only; wait for a lower close"))
        elif b.upper >= P["long_shadow"] * b.body and small_bot and b.body <= 0.35 * b.rng:
            if tr == "down":
                out.append(("Inverted hammer", "bullish", "long upper shadow after a fall: needs a higher close to confirm"))
            elif tr == "up":
                out.append(("Shooting star", "bearish", "long upper shadow after a rise: buyers were rejected"))
        elif is_small(b) and b.upper > b.body and b.lower > b.body:
            out.append(("Spinning top", "neutral", "small body with shadows both sides: indecision"))
    return out


def two_bar(bars, i, tr):
    a, b = bars[i - 1], bars[i]
    out = []
    if a.rng <= 0 or b.rng <= 0:
        return out
    if a.down and b.up and b.o <= a.c and b.c >= a.o and b.body > a.body:
        out.append(("Bullish engulfing", "bullish" if tr == "down" else "neutral",
                    "green body swallows the previous red body" + ("" if tr == "down" else " (no downtrend before it, so weak)")))
    if a.up and b.down and b.o >= a.c and b.c <= a.o and b.body > a.body:
        out.append(("Bearish engulfing", "bearish" if tr == "up" else "neutral",
                    "red body swallows the previous green body" + ("" if tr == "up" else " (no uptrend before it, so weak)")))
    if is_long(a) and b.top < a.top and b.bot > a.bot and b.body < a.body and not (is_doji(b)):
        if a.down and b.up:
            out.append(("Bullish harami", "bullish" if tr == "down" else "neutral", "small green body inside a big red one"))
        elif a.up and b.down:
            out.append(("Bearish harami", "bearish" if tr == "up" else "neutral", "small red body inside a big green one"))
    if is_long(a) and is_doji(b) and b.top < a.top and b.bot > a.bot:
        out.append(("Harami cross", "bullish" if a.down else "bearish", "doji inside a big body: the move stalled"))
    if a.down and is_long(a) and b.up and b.o < a.c and a.c < b.c < a.o and b.c > a.mid:
        out.append(("Piercing line", "bullish" if tr == "down" else "neutral", "green bar opens below and closes above the middle of the red bar"))
    if a.up and is_long(a) and b.down and b.o > a.c and a.o < b.c < a.c and b.c < a.mid:
        out.append(("Dark cloud cover", "bearish" if tr == "up" else "neutral", "red bar opens above and closes below the middle of the green bar"))
    tol = 0.1 * max(a.rng, b.rng)
    if abs(a.h - b.h) <= tol and a.up and b.down and tr == "up":
        out.append(("Tweezer top", "bearish", "two highs at the same level after a rise"))
    if abs(a.l - b.l) <= tol and a.down and b.up and tr == "down":
        out.append(("Tweezer bottom", "bullish", "two lows at the same level after a fall"))
    if b.h < a.h and b.l > a.l:
        out.append(("Inside bar", "neutral", "range inside the previous bar: a breakout either way is the signal"))
    elif b.h > a.h and b.l < a.l:
        out.append(("Outside bar", "neutral", "range engulfs the previous bar: volatility expanding"))
    return out


def three_bar(bars, i, tr):
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    out = []
    if min(a.rng, b.rng, c.rng) <= 0:
        return out
    if a.down and is_long(a) and is_small(b) and c.up and c.c > a.mid and b.top < a.c + 0.5 * a.body:
        out.append(("Morning star", "bullish" if tr in ("down", "flat") else "neutral", "big red, small indecisive bar, big green back above the middle"))
    if a.up and is_long(a) and is_small(b) and c.down and c.c < a.mid and b.bot > a.c - 0.5 * a.body:
        out.append(("Evening star", "bearish" if tr in ("up", "flat") else "neutral", "big green, small indecisive bar, big red back below the middle"))
    if all(x.up for x in (a, b, c)) and a.c < b.c < c.c and a.o <= b.o <= a.c and b.o <= b.c and b.o <= c.o <= b.c and \
            all(x.body >= 0.5 * x.rng for x in (a, b, c)):
        out.append(("Three white soldiers", "bullish", "three strong green bars, each closing higher"))
    if all(x.down for x in (a, b, c)) and a.c > b.c > c.c and a.c <= b.o <= a.o and b.c <= c.o <= b.o and \
            all(x.body >= 0.5 * x.rng for x in (a, b, c)):
        out.append(("Three black crows", "bearish", "three strong red bars, each closing lower"))
    return out


def _b(tr, need, bias):
    """bias only when the trend before the pattern is the one it needs, else neutral"""
    return bias if tr == need else "neutral"


def extra_one(bars, i, tr):
    b = bars[i]
    out = []
    if b.rng <= 0:
        return out
    if is_long(b) and b.up and b.lower <= 0.05 * b.rng and b.upper <= 0.25 * b.rng:
        out.append(("Bullish belt hold", _b(tr, "down", "bullish"), "opens at the low and runs up: sudden buying after a fall"))
    if is_long(b) and b.down and b.upper <= 0.05 * b.rng and b.lower <= 0.25 * b.rng:
        out.append(("Bearish belt hold", _b(tr, "up", "bearish"), "opens at the high and runs down: sudden selling after a rise"))
    prev = [x.rng for x in bars[max(0, i - 10):i] if x.rng > 0]
    if prev and is_small(b) and b.upper >= 0.3 * b.rng and b.lower >= 0.3 * b.rng and b.rng >= 1.5 * sum(prev) / len(prev):
        out.append(("High wave", "neutral", "small body with very long shadows on a wide bar: high volatility, no decision"))
    return out


def extra_two(bars, i, tr):
    a, b = bars[i - 1], bars[i]
    out = []
    if a.rng <= 0 or b.rng <= 0:
        return out
    if a.down and is_long(a) and b.up and is_long(b) and b.o >= a.o:
        out.append(("Bullish kicker", "bullish", "after a big red bar, price gaps up past its open and closes strongly: sentiment flipped"))
    if a.up and is_long(a) and b.down and is_long(b) and b.o <= a.o:
        out.append(("Bearish kicker", "bearish", "after a big green bar, price gaps down past its open and closes strongly: sentiment flipped"))
    tol = 0.05 * max(a.rng, b.rng)
    if a.down and b.up and abs(b.o - a.o) <= tol:
        out.append(("Bullish separating lines", _b(tr, "up", "bullish"), "both bars open at the same price, then price goes up: the uptrend resumes"))
    if a.up and b.down and abs(b.o - a.o) <= tol:
        out.append(("Bearish separating lines", _b(tr, "down", "bearish"), "both bars open at the same price, then price goes down: the downtrend resumes"))
    if is_long(a) and is_long(b) and abs(b.c - a.c) <= tol:
        if a.down and b.up:
            out.append(("Bullish meeting lines", _b(tr, "down", "bullish"), "a big green bar closes right where the big red bar closed"))
        elif a.up and b.down:
            out.append(("Bearish meeting lines", _b(tr, "up", "bearish"), "a big red bar closes right where the big green bar closed"))
    if tr == "down" and a.down and is_long(a) and b.up and b.o < a.c:
        d = b.c - a.c
        if abs(d) <= 0.05 * a.rng:
            out.append(("On-neck", "bearish", "weak bounce closes at the prior close: the fall probably continues"))
        elif 0.05 * a.rng < d <= 0.15 * a.rng:
            out.append(("In-neck", "bearish", "weak bounce closes just inside the prior body: the fall probably continues"))
        elif d > 0.15 * a.rng and b.c < a.mid:
            out.append(("Thrusting", "bearish", "bounce closes inside the prior body but below its middle: the fall probably continues"))
    if is_long(a) and is_doji(b):
        if a.up and b.bot >= a.c:
            out.append(("Bearish doji star", _b(tr, "up", "bearish"), "doji gaps up above a big green bar: buyers hesitate"))
        elif a.down and b.top <= a.c:
            out.append(("Bullish doji star", _b(tr, "down", "bullish"), "doji gaps down below a big red bar: sellers hesitate"))
    return out


def extra_three(bars, i, tr):
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    out = []
    if min(a.rng, b.rng, c.rng) <= 0:
        return out
    inside = b.top < a.top and b.bot > a.bot
    if a.down and is_long(a) and b.up and inside and c.up and c.c > a.o:
        out.append(("Three inside up", _b(tr, "down", "bullish"), "harami, then a green bar closes above the first bar's open: confirmed reversal"))
    if a.up and is_long(a) and b.down and inside and c.down and c.c < a.o:
        out.append(("Three inside down", _b(tr, "up", "bearish"), "harami, then a red bar closes below the first bar's open: confirmed reversal"))
    if a.down and b.up and b.o <= a.c and b.c >= a.o and b.body > a.body and c.up and c.c > b.c:
        out.append(("Three outside up", _b(tr, "down", "bullish"), "bullish engulfing, then another higher close: confirmed reversal"))
    if a.up and b.down and b.o >= a.c and b.c <= a.o and b.body > a.body and c.down and c.c < b.c:
        out.append(("Three outside down", _b(tr, "up", "bearish"), "bearish engulfing, then another lower close: confirmed reversal"))
    if a.down and is_long(a) and is_doji(b) and b.h < a.l and c.up and is_long(c) and c.l > b.h:
        out.append(("Bullish abandoned baby", "bullish", "doji gapped clear below and the next bar gapped clear above it: rare, strong reversal"))
    if a.up and is_long(a) and is_doji(b) and b.l > a.h and c.down and is_long(c) and c.h < b.l:
        out.append(("Bearish abandoned baby", "bearish", "doji gapped clear above and the next bar gapped clear below it: rare, strong reversal"))
    if a.up and is_long(a) and b.down and is_small(b) and b.bot > a.top and c.down and c.o >= b.o and c.c <= b.c and c.c > a.c:
        out.append(("Upside gap two crows", _b(tr, "up", "bearish"), "two red bars after a gap up, still above the first close: buyers are tiring"))
    if a.down and b.up and c.down and abs(c.c - a.c) <= 0.05 * a.rng and b.c > a.o:
        out.append(("Stick sandwich", _b(tr, "down", "bullish"), "two red bars close at the same price with a green bar between: support"))
    if a.up and b.up and b.bot > a.top and c.down and c.o <= b.top and c.o >= b.bot and a.top < c.c < b.bot:
        out.append(("Upside Tasuki gap", _b(tr, "up", "bullish"), "after a gap up, a red bar closes inside the gap but does not fill it: uptrend continues"))
    if a.down and b.down and b.top < a.bot and c.up and c.o >= b.bot and c.o <= b.top and b.top < c.c < a.bot:
        out.append(("Downside Tasuki gap", _b(tr, "down", "bearish"), "after a gap down, a green bar closes inside the gap but does not fill it: downtrend continues"))
    if all(is_doji(x) for x in (a, b, c)):
        if b.mid < a.mid and b.mid < c.mid:
            out.append(("Tri-star (bullish)", _b(tr, "down", "bullish"), "three dojis with the middle one lowest: selling is exhausted"))
        elif b.mid > a.mid and b.mid > c.mid:
            out.append(("Tri-star (bearish)", _b(tr, "up", "bearish"), "three dojis with the middle one highest: buying is exhausted"))
    if a.down and is_long(a) and b.down and b.top <= a.top and b.c > a.c and b.l < a.l and b.lower >= 2 * max(b.body, 1e-9) \
            and c.up and c.c < b.c and c.o >= b.l:
        out.append(("Unique three river bottom", _b(tr, "down", "bullish"), "a lower low with a long shadow, then a small green bar: selling is fading"))
    return out


def extra_multi(bars, i, tr):
    out = []
    if i >= 3:
        a, b, c, d = bars[i - 3:i + 1]
        if a.up and b.up and c.up and a.c < b.c < c.c and d.down and d.o >= c.c and d.c <= a.o:
            out.append(("Bullish three-line strike", _b(tr, "up", "bullish"), "three green bars, then one red bar undoes them all: usually a pause before the rise resumes"))
        if a.down and b.down and c.down and a.c > b.c > c.c and d.up and d.o <= c.c and d.c >= a.o:
            out.append(("Bearish three-line strike", _b(tr, "down", "bearish"), "three red bars, then one green bar undoes them all: usually a pause before the fall resumes"))
    if i >= 4:
        a, m1, m2, m3, e = bars[i - 4:i + 1]
        mid = (m1, m2, m3)
        if a.up and is_long(a) and e.up and is_long(e) and all(m.body < a.body and m.h <= a.h and m.l >= a.l for m in mid) \
                and e.c > a.h and tr != "down":
            holds = m1.o > a.c and all(m.l > a.mid for m in mid)
            out.append(("Mat hold" if holds else "Rising three methods", _b(tr, "up", "bullish"),
                        "a big green bar, three small bars that stay inside its range, then a green bar to a new high: uptrend continues"))
        if a.down and is_long(a) and e.down and is_long(e) and all(m.body < a.body and m.h <= a.h and m.l >= a.l for m in mid) \
                and e.c < a.l and tr != "up":
            out.append(("Falling three methods", _b(tr, "down", "bearish"),
                        "a big red bar, three small bars that stay inside its range, then a red bar to a new low: downtrend continues"))
        c3 = bars[i - 3]
        a1, b1, c1, d1 = bars[i - 4], bars[i - 3], bars[i - 2], bars[i - 1]
        if a1.down and b1.down and c1.down and a1.c > b1.c > c1.c and d1.down and d1.upper >= d1.body and e.up and e.o > d1.o and e.c > d1.h:
            out.append(("Ladder bottom", _b(tr, "down", "bullish"), "three falling red bars, a weak red bar with a top shadow, then a strong green bar: selling is ending"))
    return out


def scan(bars, start=0):
    """-> list of dicts {index, date, pattern, bias, trend, note, volume_ratio}"""
    found = []
    for i in range(max(start, 0), len(bars)):
        tr = trend(bars, i)
        hits = one_bar(bars, i, tr) + extra_one(bars, i, tr)
        if i >= 1:
            hits += two_bar(bars, i, tr) + extra_two(bars, i, tr)
        if i >= 2:
            hits += three_bar(bars, i, tr) + extra_three(bars, i, tr)
        hits += extra_multi(bars, i, tr)
        vols = [x.v for x in bars[max(0, i - 20):i] if x.v]
        vr = round(bars[i].v / (sum(vols) / len(vols)), 2) if vols and bars[i].v else None
        for name, bias, note in hits:
            found.append({"index": i, "date": bars[i].d, "pattern": name, "bias": bias, "trend": tr,
                          "note": note, "volume_ratio": vr})
    return found


def stats(bars, horizon):
    """For each pattern with a bias, the average % move over the next `horizon` closes
    (sign flipped for bearish so a positive number always means 'the hint was right')."""
    res = {}
    for f in scan(bars):
        if f["bias"] == "neutral" or f["index"] + horizon >= len(bars):
            continue
        c0, c1 = bars[f["index"]].c, bars[f["index"] + horizon].c
        pct = (c1 - c0) / c0 * 100.0 * (1 if f["bias"] == "bullish" else -1)
        r = res.setdefault(f["pattern"], [])
        r.append(pct)
    return {k: {"count": len(v), "hit_rate_pct": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1),
                "avg_move_pct": round(sum(v) / len(v), 2)} for k, v in res.items()}


RULE_TEXT = [
    "Trend before a pattern: change of the previous %d closes, >= %.1f%% up or down, else flat." % (P["trend_bars"], P["trend_pct"]),
    "Doji: body <= 10% of the range. Marubozu: body >= 60% and both shadows <= 5% of the range.",
    "Hammer/Hanging man: lower shadow >= 2x body, little upper shadow, small body; Hammer after a fall, Hanging man after a rise.",
    "Inverted hammer/Shooting star: the same upside-down.",
    "Engulfing: the body fully covers the previous opposite body. Harami: a smaller body inside the previous long body.",
    "Piercing line/Dark cloud: closes beyond the middle of the previous long body after a gap-style open.",
    "Morning/Evening star: long bar, small bar, long opposite bar past the first bar's middle.",
    "Three white soldiers/black crows: three strong bars in a row, each closing further.",
    "Tweezer: two consecutive highs (or lows) within 10% of the range, after a rise (or fall). Inside/Outside bar: range inside/around the last.",
    "Long-legged doji: a doji with both shadows >= 35% of the range. Soldiers/crows: each bar opens inside the previous body.",
    "Also: kickers, belt holds, separating/meeting lines, on-neck/in-neck/thrusting, doji stars, three inside/outside, abandoned baby, upside gap two crows,",
    "stick sandwich, Tasuki gaps, tri-star, unique three river bottom, three-line strike, rising/falling three methods, mat hold, ladder bottom, high wave.",
    "Not covered: Breakaway and Concealing baby swallow (sources disagree on the rules) and chart patterns (flags, triangles, pennants), which are not candle patterns.",
    "A bias of 'neutral' means the pattern lacks the trend it needs, or is indecision.",
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Candlestick patterns (trader-candles).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan"); p.add_argument("file"); p.add_argument("--last", type=int, default=5, help="only the last N bars")
    p.add_argument("--timeframe", default="", help="label for the output, e.g. day, 15min")
    p.add_argument("--bias", choices=["bullish", "bearish", "neutral"], help="show only this bias")
    p.add_argument("--pattern", help="show only patterns whose name contains this text, e.g. engulfing")
    p = sub.add_parser("stats"); p.add_argument("file"); p.add_argument("--horizon", type=int, default=5)
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join(RULE_TEXT)); return 0
    if a.cmd == "scan":
        bars = load(a.file)
        found = scan(bars, len(bars) - max(a.last, 1))
        if a.bias:
            found = [f for f in found if f["bias"] == a.bias]
        if a.pattern:
            found = [f for f in found if a.pattern.lower() in f["pattern"].lower()]
        label = " (%s)" % a.timeframe if a.timeframe else ""
        print("%d bars%s, last %d checked" % (len(bars), label, min(a.last, len(bars))))
        if not found:
            print("No pattern in the last bars.")
        for f in found:
            vol = "" if f["volume_ratio"] is None else ", volume %.1fx its 20-bar average" % f["volume_ratio"]
            print("%s  %-22s %-8s after %s trend%s\n    %s" % (f["date"], f["pattern"], f["bias"].upper(), f["trend"], vol, f["note"]))
        print("\nA pattern is a hint, not a signal: check trend, a nearby level, volume and your stop. Not financial advice.")
        return 0
    if a.cmd == "stats":
        bars = load(a.file)
        st = stats(bars, a.horizon)
        if not st:
            print("Not enough history for any pattern."); return 1
        print("Next %d bars after each pattern, in this file only (%d bars). 'hit' = moved the way the hint said." % (a.horizon, len(bars)))
        for k, v in sorted(st.items(), key=lambda kv: -kv[1]["count"]):
            print("  %-22s n=%-3d hit %5.1f%%  avg %+.2f%%" % (k, v["count"], v["hit_rate_pct"], v["avg_move_pct"]))
        print("\nSmall counts mean nothing. Treat hit rates near 50%% as no edge. Costs not included.")
        return 0
    ap.print_help(); return 1


if __name__ == "__main__":
    sys.exit(main())
