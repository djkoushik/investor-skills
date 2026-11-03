"""Shared helpers for the horizon skills (long-term, swing, F&O). Standard library only.

Every horizon skill folder carries an identical copy of this file, so each skill can be installed
on its own. A test checks that the copies are identical: edit one, then copy it to the others.
"""
import csv
import datetime
import json
import math
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class TaError(Exception):
    pass


# ------------------------------------------------------------ symbols, time, files

def clean_symbol(text):
    t = str(text or "").strip().upper()
    t = re.sub(r"^(NSE|BSE):", "", t)
    t = re.sub(r"\.(NS|BO)$", "", t)
    if not re.match(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$", t):
        raise TaError("%r does not look like an NSE symbol" % text)
    return t


def india_now(text=None):
    """Now in India time (a naive datetime). `text` like "2026-10-26 09:30" is taken as India time."""
    if not text:
        return datetime.datetime.now(IST).replace(tzinfo=None)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise TaError('time must look like "2026-10-26 09:30" (India time)')


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TaError("%s is not valid JSON: %s" % (path, e))


def save_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def read_text(path):
    return sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ bars

class Bar:
    __slots__ = ("d", "o", "h", "l", "c", "v")

    def __init__(self, d, o, h, l, c, v=0.0):
        self.d, self.o, self.h, self.l, self.c, self.v = d, o, h, l, c, v


def load_bars(path):
    """Oldest first. CSV (date,open,high,low,close[,volume]) or JSON: a list of such objects,
    Kite's {"candles": [[date,o,h,l,c,v], ...]} or a bare list of rows."""
    text = read_text(path)
    t = text.lstrip()
    rows = []
    if t[:1] in "[{":
        data = json.loads(t)
        if isinstance(data, dict):
            data = data.get("candles") or data.get("data", {}).get("candles") or data.get("bars") or []
        for r in data:
            if isinstance(r, dict):
                low = {k.lower(): v for k, v in r.items()}
                rows.append((low.get("date") or low.get("timestamp") or "", low["open"], low["high"],
                             low["low"], low["close"], low.get("volume", 0)))
            else:
                rows.append(tuple(r) + (0,) * (6 - len(r)))
    else:
        for r in csv.DictReader(text.splitlines()):
            low = {k.strip().lower(): v for k, v in r.items()}
            rows.append((low.get("date") or low.get("timestamp") or "", low["open"], low["high"],
                         low["low"], low["close"], low.get("volume") or 0))
    bars = []
    for d, o, h, l, c, v in rows:
        try:
            b = Bar(str(d)[:10], float(o), float(h), float(l), float(c), float(v or 0))
        except (TypeError, ValueError):
            continue
        if b.h < max(b.o, b.c) - 1e-9 or b.l > min(b.o, b.c) + 1e-9:
            raise TaError("bad bar on %s: high/low do not contain open/close" % d)
        bars.append(b)
    if len(bars) < 2:
        raise TaError("need at least 2 bars")
    return bars


def resample(bars, rule):
    """Daily bars -> weekly ('W', ISO week) or monthly ('M') bars."""
    out, key = [], None
    for b in bars:
        try:
            dt = datetime.date.fromisoformat(b.d[:10])
        except ValueError:
            raise TaError("bar date %r is not YYYY-MM-DD" % b.d)
        k = (dt.isocalendar()[0], dt.isocalendar()[1]) if rule == "W" else (dt.year, dt.month)
        if k != key:
            out.append(Bar(b.d, b.o, b.h, b.l, b.c, b.v))
            key = k
        else:
            a = out[-1]
            a.h, a.l, a.c, a.v = max(a.h, b.h), min(a.l, b.l), b.c, a.v + b.v
    return out


# ------------------------------------------------------------ indicators (each returns a list, None-padded)

def sma(vals, n):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(vals, n=14):
    out = [None] * len(vals)
    if len(vals) <= n:
        return out
    gains = [max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals))]
    losses = [max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(vals)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def atr(bars, n=14):
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i - 1].c if i else b.c
        trs.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    if len(bars) < n:
        return out
    a = sum(trs[:n]) / n
    out[n - 1] = a
    for i in range(n, len(bars)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def last(series):
    for v in reversed(series):
        if v is not None:
            return v
    return None


# ------------------------------------------------------------ support / resistance

def pivots(bars, k=3):
    """Indexes of confirmed pivot highs and lows (extreme of the 2k+1 bars around them)."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i].h >= max(b.h for b in win) and bars[i].h > bars[i - 1].h:
            highs.append(i)
        if bars[i].l <= min(b.l for b in win) and bars[i].l < bars[i - 1].l:
            lows.append(i)
    return highs, lows


def zones(levels, tol_pct=1.5):
    """Cluster price levels [(price, index)] that sit within tol_pct of each other.
    -> [{"low","high","mid","touches","last_index"}] sorted by price."""
    pts = sorted(levels)
    out, cur = [], []
    for p, i in pts:
        if cur and p <= (sum(x[0] for x in cur) / len(cur)) * (1 + tol_pct / 100.0):
            cur.append((p, i))
        else:
            if cur:
                out.append(cur)
            cur = [(p, i)]
    if cur:
        out.append(cur)
    return [{"low": min(x[0] for x in c), "high": max(x[0] for x in c),
             "mid": sum(x[0] for x in c) / len(c), "touches": len(c), "last_index": max(x[1] for x in c)} for c in out]


def support_resistance(bars, k=3, tol_pct=1.5, lookback=None):
    b = bars[-lookback:] if lookback else bars
    off = len(bars) - len(b)
    hi, lo = pivots(b, k)
    res = zones([(b[i].h, i + off) for i in hi], tol_pct)
    sup = zones([(b[i].l, i + off) for i in lo], tol_pct)
    return sup, res


def floor_pivots(bar):
    """Classic floor-trader levels for the NEXT session from this (completed) bar."""
    p = (bar.h + bar.l + bar.c) / 3.0
    return {"R2": p + (bar.h - bar.l), "R1": 2 * p - bar.l, "P": p, "S1": 2 * p - bar.h, "S2": p - (bar.h - bar.l)}


def drawdown_pct(bars):
    hi = max(b.h for b in bars)
    return (hi - bars[-1].c) / hi * 100.0, hi


# ------------------------------------------------------------ more indicators

def macd(vals, fast=12, slow=26, signal=9):
    """-> (macd line, signal line, histogram), each a None-padded list."""
    ef, es = ema(vals, fast), ema(vals, slow)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(ef, es)]
    firsts = [i for i, v in enumerate(line) if v is not None]
    sig = [None] * len(vals)
    if firsts and len(vals) - firsts[0] >= signal:
        start = firsts[0]
        tail = ema([v for v in line[start:]], signal)
        for i, v in enumerate(tail):
            sig[start + i] = v
    hist = [(a - b) if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(vals, n=20, k=2.0):
    """-> (middle, upper, lower, bandwidth_pct), each None-padded."""
    mid = sma(vals, n)
    up, lo, bw = [None] * len(vals), [None] * len(vals), [None] * len(vals)
    for i in range(n - 1, len(vals)):
        w = vals[i - n + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        up[i], lo[i] = m + k * sd, m - k * sd
        bw[i] = (up[i] - lo[i]) / m * 100.0 if m else None
    return mid, up, lo, bw


def hist_vol(closes, n=20):
    """Realised volatility, percent a year, from the last n daily log returns (252 trading days)."""
    if len(closes) <= n:
        return None
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    m = sum(r) / n
    return math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1)) * math.sqrt(252) * 100.0


def percentile_rank(value, history):
    """Share of the history at or below value, 0 to 100."""
    h = [x for x in history if x is not None]
    return 100.0 * sum(1 for x in h if x <= value) / len(h) if h else None


def range_rank(value, history):
    """(value - low) / (high - low) over the history, 0 to 100 (the 'IV rank' formula)."""
    h = [x for x in history if x is not None]
    if not h or max(h) == min(h):
        return None
    return 100.0 * (value - min(h)) / (max(h) - min(h))


def volume_pressure(bars, n=20):
    """Volume on up days divided by volume on down days over the last n bars (above 1 = buyers heavier)."""
    up = sum(b.v for b in bars[-n:] if b.c > b.o)
    dn = sum(b.v for b in bars[-n:] if b.c < b.o)
    return (up / dn) if dn else None
