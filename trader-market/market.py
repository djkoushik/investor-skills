#!/usr/bin/env python3
"""The Indian market profile shared by every trader-* tool. Standard library only.

One place for everything that is "the market" and not "the method": the NSE clock
and holiday calendar, India Standard Time, rupee formatting, the tick size, NSE
ticker rules, Yahoo and Kite symbol names, circuit-limit hints and the cost model
(brokerage, STT, exchange fees, GST, stamp duty). The other tools import this
file, so they all agree on what time it is and what a trade costs.

    python3 market.py clock                      # is the NSE open? next open
    python3 market.py clock --now "2026-10-26 09:20"
    python3 market.py costs --buy 1424.60 --sell 1433.40 --qty 20
    python3 market.py symbol "M&M" reliance.ns NSE:INFY
    python3 market.py holidays                   # the calendar the tools use

Copy the whole trader-market/ folder next to the other trader-* folders: every
tool finds this file at ../trader-market/market.py.

Market facts (verify before you rely on them, they change):
  * NSE cash segment: pre-open 9:00 to 9:08 (order entry), 9:15 to 15:30 normal
    market, India Standard Time (UTC+5:30, no daylight saving). There are no
    early closes on regular trading days. Muhurat trading on Diwali is a special
    evening session with hours NSE announces late: this tool treats it as closed.
  * Holidays: the NSE trading-holiday list for 2026 (read 2026-10-24 from
    cleartax.in/s/nse-holidays-2026, which mirrors NSE's circular). 2027 is not
    published yet. Add days in trader/holidays.json (see load_overrides).
  * Intraday (MIS) positions are squared off by your broker before the close:
    Zerodha squares off at 15:12 (stocks under extra surveillance, "CAS") and
    15:25 (the rest), with a Rs 50 + GST charge per order squared off. The
    default flat_by of 15:10 sits in front of both.
  * Tick size differs by stock (Kite shows Rs 0.10 for RELIANCE at about Rs 1,190, Rs 0.05 for many others, Rs 0.01 for ETFs).
    The tools read it per stock from trader/instruments.json and assume Rs 0.05 when the stock is not there.
  * A share can hit a circuit limit (2, 5, 10 or 20 percent band) and stop
    trading for the day: you may be unable to exit.

Costs, intraday equity on NSE (Zerodha's published schedule, read 2026-10-24
from zerodha.com/charges; other brokers differ, edit "costs" in
trader/config.json):
  brokerage     0.03 % or Rs 20 per executed order, whichever is lower, each leg
  STT           0.025 % of the turnover, sell leg only
  NSE charges   0.00307 % of the turnover, each leg
  SEBI fee      Rs 10 per crore of turnover, each leg
  stamp duty    0.003 % of the turnover, buy leg only
  IPFT          Rs 0.01 per crore, each leg
  GST           18 % of brokerage + NSE charges + SEBI fee + IPFT
"""

import datetime
import json
import math
import os
import re
import sys
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


def _utf8():
    """Windows consoles default to a code page that cannot print the rupee sign."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_utf8()

EXCHANGE = "NSE"
TZ_LABEL = "IST"
TZ_NAME = "India"
UTC_OFFSET = datetime.timedelta(hours=5, minutes=30)   # no daylight saving, ever
CURRENCY = "INR"
RUPEE = "₹"

PRE_OPEN = datetime.time(9, 0)       # order entry; the pre-open call auction matches at 9:08
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
EARLY = CLOSE                        # NSE has no early closes on regular trading days
SESSION_MINUTES = 375                # 9:15 to 15:30
BARS_5M = SESSION_MINUTES // 5       # 75

TICK = Decimal("0.05")
DEFAULT_FLAT_BY = "15:10"            # in front of the broker's MIS square-off (15:12 / 15:25 at Zerodha)
DEFAULT_NO_ENTRIES_BEFORE = "09:30"  # the end of the 15-minute opening range
DEFAULT_NO_ENTRIES_AFTER = "14:30"   # leaves 40 minutes before flat_by

NSE_HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
# Weekend holidays and special sessions: kept for the clock's explanations.
NSE_WEEKEND_HOLIDAYS = {
    "2026-02-15": "Mahashivratri (Sunday)",
    "2026-03-21": "Id-Ul-Fitr (Saturday)",
    "2026-08-15": "Independence Day (Saturday)",
    "2026-11-08": "Diwali Laxmi Pujan (Sunday): Muhurat trading, hours announced by NSE",
}
NSE_EARLY_CLOSE = {}                 # always empty: the NSE has no early closes (kept so old imports work)
SPECIAL_OPEN = {}                    # weekend days the NSE trades anyway (the Union Budget); see load_overrides
CALENDAR_YEARS = [2026]


class MarketError(Exception):
    pass


def load_overrides(path=None):
    """Merge trader/holidays.json (or $TRADER_HOLIDAYS) into the calendar:
        {"holidays": {"2027-01-26": "Republic Day"},
         "special_open": {"2027-02-01": "Union Budget"},
         "years": [2027]}
    Called on import. A bad file is ignored with a note on stderr."""
    path = path or os.environ.get("TRADER_HOLIDAYS") or os.path.join("trader", "holidays.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        for k, target in (("holidays", NSE_HOLIDAYS), ("special_open", SPECIAL_OPEN)):
            for day, why in (doc.get(k) or {}).items():
                datetime.date.fromisoformat(day)
                target[day] = str(why)
        for y in doc.get("years") or []:
            if int(y) not in CALENDAR_YEARS:
                CALENDAR_YEARS.append(int(y))
    except (ValueError, OSError, AttributeError, TypeError) as e:
        print("market.py: ignored %s (%s)" % (path, e), file=sys.stderr)
        return False
    return True


load_overrides()


# ---------------------------------------------------------------- instruments (per-stock tick, circuits)

INSTRUMENTS = {}


def load_instruments(path=None):
    """Read trader/instruments.json (or $TRADER_INSTRUMENTS): {"RELIANCE": {"tick_size": 0.1,
    "lower_circuit": 1071, "upper_circuit": 1309, "lot_size": 1, "instrument_token": 738561, ...}}.
    Written by `market.py instrument add FILE` from Kite's search_instruments result."""
    path = path or os.environ.get("TRADER_INSTRUMENTS") or os.path.join("trader", "instruments.json")
    INSTRUMENTS.clear()
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            INSTRUMENTS.update({k.upper(): v for k, v in json.load(fh).items()})
    except (ValueError, OSError, AttributeError, TypeError) as e:
        print("market.py: ignored %s (%s)" % (path, e), file=sys.stderr)
        return False
    return True


def instrument(symbol):
    try:
        code = parse_symbol(symbol)[0]
    except Exception:
        return None
    return INSTRUMENTS.get(code)


def tick_known(symbol):
    i = instrument(symbol)
    return bool(i and i.get("tick_size"))


def tick_for(symbol):
    """The tick size for a stock: from the saved Kite instrument, else Rs 0.05."""
    i = instrument(symbol)
    if i and i.get("tick_size"):
        return Decimal(str(i["tick_size"]))
    return TICK


def import_instruments(doc, path=None):
    """Merge a Kite search_instruments result ({"data": [...]} or a list) into instruments.json.
    Keeps NSE/BSE equity rows only. Returns the symbols added."""
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise MarketError("expected Kite's search_instruments JSON ({\"data\": [...]})")
    path = path or os.environ.get("TRADER_INSTRUMENTS") or os.path.join("trader", "instruments.json")
    cur = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cur = json.load(fh)
    added = []
    for r in rows:
        if r.get("exchange") not in ("NSE", "BSE") or r.get("instrument_type") != "EQ":
            continue
        sym = str(r.get("tradingsymbol", "")).upper()
        if not sym or not r.get("tick_size"):
            continue
        cur[sym] = {"exchange": r["exchange"], "tick_size": r["tick_size"], "lot_size": r.get("lot_size", 1),
                    "instrument_token": r.get("instrument_token"), "isin": r.get("isin"), "name": r.get("name"),
                    "lower_circuit": r.get("lower_circuit_limit"), "upper_circuit": r.get("upper_circuit_limit"),
                    "freeze_quantity": r.get("freeze_quantity")}
        added.append(sym)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2)
        fh.write("\n")
    load_instruments(path)
    return added


load_instruments()


# ---------------------------------------------------------------- time

def to_local(utc):
    """Naive UTC -> naive India Standard Time."""
    return utc.replace(tzinfo=None) + UTC_OFFSET


def local_to_utc(local):
    return local - UTC_OFFSET


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def parse_now(text, error=MarketError, flag="--now"):
    """ISO time -> naive UTC. A time without an offset is India time; "10:15" alone means today."""
    if not text:
        return utc_now()
    t = str(text).strip()
    for suffix in (" IST", " ist"):
        if t.endswith(suffix):
            t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_local(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise error('%s wants an India time like "2026-10-26 10:15" (or ...Z for UTC)' % flag)
    if dt.tzinfo is None:
        return local_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason). Weekends (unless a special session), the NSE holiday list."""
    iso = d.isoformat()
    if iso in SPECIAL_OPEN:
        return True, ""
    if d.weekday() >= 5:
        return False, "weekend" + (": " + NSE_WEEKEND_HOLIDAYS[iso] if iso in NSE_WEEKEND_HOLIDAYS else "")
    name = NSE_HOLIDAYS.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in India time, or None if closed all day."""
    ok, _ = trading_day(d)
    return (OPEN, CLOSE) if ok else None


def next_open_date(d):
    nd = d + datetime.timedelta(days=1)
    for _ in range(20):
        if session(nd):
            return nd
        nd += datetime.timedelta(days=1)
    return None


def next_open(d):
    nd = next_open_date(d)
    return "%s 09:15" % nd.isoformat() if nd else None


def market_status(now_utc):
    local = to_local(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_india": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in CALENDAR_YEARS,
          "open": False, "pre_open": False, "reason": reason, "early_close": NSE_EARLY_CLOSE.get(d.isoformat())}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        t = local.time()
        if o <= t < c:
            st["open"], st["reason"] = True, "regular session"
        elif PRE_OPEN <= t < o:
            st["pre_open"], st["reason"] = True, "pre-open session (orders can be entered, normal market opens 9:15)"
        elif t < o:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    nd = d if (s and local.time() < s[0]) else (next_open_date(d) or d)
    st["next_open"] = "%s 09:15" % nd.isoformat()
    return st


# ---------------------------------------------------------------- money and prices

def D(x):
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x).strip().lstrip(RUPEE + "$").replace(",", "").replace("Rs.", "").replace("Rs", "").strip())


def money(x, places=2):
    """Rs 1,424.60 -> the rupee sign with Indian digit grouping (12,34,567.50)."""
    q = Decimal(1).scaleb(-places)
    v = D(x).quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if v < 0 else ""
    s = format(abs(v), "f")
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s%s%s%s" % (sign, RUPEE, whole, ("." + frac) if frac else "")


def tick_round(x, mode="nearest", tick=TICK):
    """A price on the tick grid: 'down' (a long's stop), 'up', or 'nearest' (half up)."""
    tick = D(tick)
    rounding = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}[mode]
    return ((D(x) / tick).to_integral_value(rounding=rounding) * tick).quantize(Decimal("0.01"))


def on_tick(x, tick=TICK):
    return D(x) % D(tick) == 0


# ---------------------------------------------------------------- symbols

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
SUFFIXES = {".NS": "NSE", ".BO": "BSE"}


def parse_symbol(text):
    """'reliance', 'RELIANCE.NS', 'NSE:RELIANCE', 'BSE:500325', 'M&M' -> (code, exchange).
    Raises MarketError for something that cannot be an Indian ticker."""
    t = str(text or "").strip().upper()
    exch = "NSE"
    m = re.match(r"^(NSE|BSE):(.+)$", t)
    if m:
        exch, t = m.group(1), m.group(2).strip()
    for suf, ex in SUFFIXES.items():
        if t.endswith(suf):
            t, exch = t[: -len(suf)], ex
    if not NSE_SYMBOL.match(t):
        raise MarketError("%r does not look like an NSE ticker (letters, digits, & and -, like RELIANCE, M&M, "
                          "BAJAJ-AUTO)" % text)
    return t, exch


def valid_symbol(text):
    try:
        parse_symbol(text)
        return True
    except MarketError:
        return False


def clean_symbol(text):
    """The tool-wide form of a ticker: NSE trading symbol, upper case, no suffix."""
    return parse_symbol(text)[0]


def yahoo_symbol(text):
    """RELIANCE -> RELIANCE.NS, M&M -> M&M.NS, BSE:500325 -> 500325.BO. Indices (^NSEI) pass through."""
    t = str(text or "").strip().upper()
    if t.startswith("^"):
        return t
    code, exch = parse_symbol(t)
    return code + (".BO" if exch == "BSE" else ".NS")


def yahoo_path_symbol(text):
    """The yahoo symbol as it goes in a URL path (& must be escaped)."""
    from urllib.parse import quote
    return quote(yahoo_symbol(text), safe="^")


def kite_symbol(text):
    """(exchange, tradingsymbol) for the Kite connector."""
    code, exch = parse_symbol(text)
    return exch, code


# ---------------------------------------------------------------- circuits

BANDS = (2, 5, 10, 20)


def near_circuit(change_pct, margin=0.5):
    """The price band a day's move is brushing, or None. A share whose move is within `margin`
    points of 2/5/10/20 percent may be locked at the circuit: nothing fills at the limit."""
    c = abs(float(change_pct))
    for b in BANDS:
        if b - margin <= c <= b + 0.25:
            return b
    return None


# ---------------------------------------------------------------- costs

DEFAULT_COSTS = {
    "brokerage_pct": 0.03, "brokerage_max": 20.0,   # per executed order
    "stt_sell_pct": 0.025,
    "exchange_pct": 0.00307,
    "sebi_per_crore": 10.0,
    "stamp_buy_pct": 0.003,
    "ipft_per_crore": 0.01,
    "gst_pct": 18.0,
}


def cost_rates(config=None):
    r = dict(DEFAULT_COSTS)
    for k, v in ((config or {}).get("costs") or {}).items():
        if k in r and isinstance(v, (int, float)):
            r[k] = float(v)
    return r


def leg_costs(price, qty, side, rates=None):
    """Charges of one executed order. Returns a dict of rupee amounts."""
    r = rates or DEFAULT_COSTS
    turnover = float(price) * int(qty)
    brokerage = min(r["brokerage_max"], turnover * r["brokerage_pct"] / 100.0) if qty else 0.0
    stt = turnover * r["stt_sell_pct"] / 100.0 if side == "sell" else 0.0
    exch = turnover * r["exchange_pct"] / 100.0
    sebi = turnover * r["sebi_per_crore"] / 1e7
    ipft = turnover * r["ipft_per_crore"] / 1e7
    stamp = turnover * r["stamp_buy_pct"] / 100.0 if side == "buy" else 0.0
    gst = (brokerage + exch + sebi + ipft) * r["gst_pct"] / 100.0
    parts = {"brokerage": brokerage, "stt": stt, "exchange": exch, "sebi": sebi, "ipft": ipft,
             "stamp": stamp, "gst": gst}
    parts["total"] = sum(parts.values())
    return parts


def round_trip_costs(buy_price, sell_price, qty, rates=None):
    """Everything a buy then sell of `qty` shares costs, in rupees, with the pieces."""
    b, s = leg_costs(buy_price, qty, "buy", rates), leg_costs(sell_price, qty, "sell", rates)
    out = {k: round(b[k] + s[k], 4) for k in b}
    out["total"] = round(out["total"], 2)
    return out


def breakeven_move(price, qty, rates=None):
    """Rupees per share the price must rise just to pay the round trip (sell at price + this)."""
    if qty <= 0:
        return 0.0
    lo, hi = 0.0, float(price) * 0.05
    for _ in range(60):
        mid = (lo + hi) / 2
        net = mid * qty - round_trip_costs(price, price + mid, qty, rates)["total"]
        lo, hi = (mid, hi) if net < 0 else (lo, mid)
    return round(hi, 4)


# ---------------------------------------------------------------- command line

def _cmd_clock(a):
    st = market_status(parse_now(a.now))
    print("India %s (%s): NSE %s, %s." % (st["now_india"], st["weekday"],
                                         "OPEN" if st["open"] else "closed", st["reason"]))
    if st.get("opens"):
        print("Today's session: %s to %s." % (st["opens"], st["closes"]))
    if not st["open"]:
        print("Next open: %s IST." % st["next_open"])
    if not st["calendar_known"]:
        print("The holiday list covers %s only: check nseindia.com (Market > Holidays) or add days in "
              "trader/holidays.json." % ", ".join(map(str, CALENDAR_YEARS)))
    return 0 if st["open"] else 1


def _cmd_costs(a):
    qty = a.qty
    c = round_trip_costs(a.buy, a.sell, qty)
    gross = (a.sell - a.buy) * qty
    print("Buy %d at %s, sell at %s:" % (qty, money(a.buy), money(a.sell)))
    for k in ("brokerage", "stt", "exchange", "sebi", "ipft", "stamp", "gst"):
        print("  %-10s %s" % (k, money(c[k], 4 if c[k] < 1 else 2)))
    print("  %-10s %s   (gross %s, net %s)" % ("TOTAL", money(c["total"]), money(gross), money(gross - c["total"])))
    print("Break-even: the price must rise %s a share (%.3f %%)." % (
        money(breakeven_move(a.buy, qty), 3), 100 * breakeven_move(a.buy, qty) / a.buy))
    return 0


def _cmd_symbol(a):
    rc = 0
    for s in a.symbols:
        try:
            code, ex = parse_symbol(s)
            print("%-14s -> %s %s   yahoo %s   kite %s:%s" % (s, ex, code, yahoo_symbol(s), ex, code))
        except MarketError as e:
            print("%-14s -> %s" % (s, e))
            rc = 1
    return rc


def _cmd_holidays(a):
    print("NSE trading holidays the tools use (years known: %s):" % ", ".join(map(str, CALENDAR_YEARS)))
    for d in sorted(NSE_HOLIDAYS):
        print("  %s %s  %s" % (d, datetime.date.fromisoformat(d).strftime("%a"), NSE_HOLIDAYS[d]))
    for d in sorted(NSE_WEEKEND_HOLIDAYS):
        print("  %s %s  %s" % (d, datetime.date.fromisoformat(d).strftime("%a"), NSE_WEEKEND_HOLIDAYS[d]))
    for d in sorted(SPECIAL_OPEN):
        print("  %s (special session)  %s" % (d, SPECIAL_OPEN[d]))
    return 0


def _cmd_instrument(a):
    if a.action == "add":
        text = sys.stdin.read() if a.arg == "-" else open(a.arg, encoding="utf-8").read()
        try:
            added = import_instruments(json.loads(text))
        except json.JSONDecodeError as e:
            raise MarketError("not valid JSON: %s" % e)
        print("saved %d instrument(s): %s" % (len(added), ", ".join(added) or "none (equity rows only)"))
        return 0
    i = instrument(a.arg)
    if not i:
        print("%s is not in trader/instruments.json: tick assumed Rs %s" % (a.arg.upper(), TICK))
        return 1
    print(json.dumps(i, indent=2))
    return 0



# ---------------------------------------------------------------- Kite data (the data source of this project)
#
# Kite is reached through the connector, which only the agent can call, so the pipeline is:
#   1. the agent fetches get_historical_data / get_ohlc and saves the raw answer to a file
#   2. `market.py kite-import ...` turns it into trader/kite/<SYMBOL>.json, shaped like the chart files the
#      entry, exit, scanner, timemachine and alerts tools already read
#   3. those tools read trader/kite/ on their own whenever it exists and --offline is not given.
# Kite is read first when imported files exist; Yahoo stays on as the second source and the fallback.

KITE_DIR = os.path.join("trader", "kite")
KITE_INTERVALS = {"minute": "1m", "3minute": "3m", "5minute": "5m", "10minute": "10m", "15minute": "15m",
                  "30minute": "30m", "60minute": "60m", "day": "1d", "1m": "1m", "5m": "5m", "15m": "15m", "60m": "60m", "1d": "1d"}


def kite_default(daily=False):
    """The folder of imported Kite files (daily bars live in trader/kite/daily), or None when there is none."""
    d = os.path.join(KITE_DIR, "daily") if daily else KITE_DIR
    return d if os.path.isdir(d) and any(f.endswith(".json") for f in os.listdir(d)) else None


def yahoo_allowed():
    """Yahoo is the second source and the fallback; set INDIA_ALLOW_YAHOO=0 to switch it off."""
    return os.environ.get("INDIA_ALLOW_YAHOO") != "0"


def require_yahoo(error=None):
    """Called before every live Yahoo request. The project's source is Kite, so Yahoo must be asked for."""
    if not yahoo_allowed():
        raise (error or MarketError)(
            "no Kite data found in %s. Fetch it with the Kite connector (get_historical_data / get_ohlc), save the answer, "
            "run `market.py kite-import ...`, or pass --offline. Yahoo is switched off (INDIA_ALLOW_YAHOO=0)." % KITE_DIR)


def _kite_rows(doc):
    """Any Kite shape -> [(iso, o, h, l, c, v)]: a list of {date,open,..}, {"candles":[[..]]}, or a bare list of rows."""
    if isinstance(doc, dict):
        data = doc.get("data")
        doc = doc.get("candles") or (data.get("candles") if isinstance(data, dict) else data)
    rows = []
    for r in doc or []:
        if isinstance(r, dict):
            rows.append((r["date"], r["open"], r["high"], r["low"], r["close"], r.get("volume") or 0))
        else:
            rows.append((r[0], r[1], r[2], r[3], r[4], (r[5] if len(r) > 5 else 0) or 0))
    if not rows:
        raise MarketError("no candles in that Kite answer")
    return rows


def kite_to_chart(doc, symbol, interval="5minute", prev_close=None):
    """Kite historical answer -> a Yahoo-v8-shaped dict (so every existing reader works). Notes any gap at the close."""
    rows = _kite_rows(doc)
    gran = KITE_INTERVALS.get(interval)
    if not gran:
        raise MarketError("unknown interval %r (minute, 5minute, 15minute, 60minute, day)" % interval)
    sym = clean_symbol(symbol)
    ts, o, h, l, c, v = [], [], [], [], [], []
    for iso, a, b, d, e, vol in rows:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        ts.append(int(t.timestamp())); o.append(a); h.append(b); l.append(d); c.append(e); v.append(int(vol))
    last_day = datetime.datetime.fromtimestamp(ts[-1], datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    meta = {"symbol": yahoo_symbol(sym), "currency": "INR", "exchangeName": "NSI", "dataGranularity": gran, "source": "kite",
            "regularMarketPrice": c[-1], "regularMarketTime": ts[-1],
            "regularMarketDayHigh": max(h), "regularMarketDayLow": min(l), "gmtoffset": 19800, "timezone": "IST"}
    if prev_close:
        meta["previousClose"] = prev_close
        meta["chartPreviousClose"] = prev_close
    note = None
    if gran != "1d":
        mins = last_day.hour * 60 + last_day.minute + int(gran[:-1])
        if mins < 15 * 60 + 30:
            note = ("the last bar ends at %02d:%02d, before the 15:30 close: Kite's intraday history stops at 15:15, so the last "
                    "15 minutes are missing (checked 2026-10-24). Use a daily bar for the official close." % (mins // 60, mins % 60))
    out = {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                 "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}], "error": None}}
    return out, note


def kite_quote_to_chart(symbol, q):
    """One get_ohlc / get_quotes entry -> a Yahoo-shaped quote file for trader-alerts."""
    sym = clean_symbol(symbol)
    oh = q.get("ohlc") or {}
    price = q.get("last_price")
    if price is None:
        raise MarketError("that Kite quote has no last_price")
    t = q.get("timestamp") or q.get("last_trade_time")
    if t:
        try:
            tt = datetime.datetime.fromisoformat(str(t).replace(" ", "T"))
            if tt.tzinfo is None:
                tt = tt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            when = int(tt.timestamp())
        except ValueError:
            when = int(utc_now().timestamp())
    else:
        when = int(utc_now().timestamp())
    meta = {"symbol": yahoo_symbol(sym), "currency": "INR", "source": "kite", "regularMarketPrice": price, "regularMarketTime": when,
            "regularMarketDayHigh": oh.get("high"), "regularMarketDayLow": oh.get("low"),
            "previousClose": oh.get("close"), "chartPreviousClose": oh.get("close")}
    qd = {"open": [oh.get("open")], "high": [oh.get("high")], "low": [oh.get("low")], "close": [price], "volume": [q.get("volume") or 0]}
    return {"chart": {"result": [{"meta": meta, "timestamp": [when], "indicators": {"quote": [qd]}}], "error": None}}


def _cmd_kite_import(a):
    doc = json.load(sys.stdin) if a.file == "-" else json.load(open(a.file, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    if a.kind == "quotes":
        n = 0
        for key, q in doc.items():
            sym = key.split(":")[-1]
            ch = kite_quote_to_chart(sym, q)
            for name in ("kite-%s.json" % clean_symbol(sym),):
                json.dump(ch, open(os.path.join(a.out, name), "w", encoding="utf-8"))
            n += 1
        print("wrote %d quote file(s) to %s (read by trader-alerts)" % (n, a.out))
        return 0
    if not a.symbol:
        raise MarketError("candles needs --symbol")
    ch, note = kite_to_chart(doc, a.symbol, a.interval, a.prev_close)
    sym = clean_symbol(a.symbol)
    path = os.path.join(a.out, "%s.json" % sym)
    json.dump(ch, open(path, "w", encoding="utf-8"))
    print("wrote %d %s bars for %s to %s" % (len(ch["chart"]["result"][0]["timestamp"]), KITE_INTERVALS[a.interval], sym, path))
    if note:
        print("note: " + note)
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="The Indian market profile (trader-market).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("clock")
    p.add_argument("--now")
    p = sub.add_parser("costs")
    p.add_argument("--buy", type=float, required=True)
    p.add_argument("--sell", type=float, required=True)
    p.add_argument("--qty", type=int, required=True)
    p = sub.add_parser("symbol")
    p.add_argument("symbols", nargs="+")
    sub.add_parser("holidays")
    p = sub.add_parser("instrument", help="add: save Kite search_instruments JSON; show SYMBOL")
    p.add_argument("action", choices=["add", "show"])
    p.add_argument("arg", help="a JSON file (add, or - for stdin) or a symbol (show)")
    p = sub.add_parser("kite-import", help="turn a saved Kite connector answer into files the other tools read")
    p.add_argument("kind", choices=["candles", "quotes"]); p.add_argument("file", help="the saved JSON, or - for stdin")
    p.add_argument("--symbol"); p.add_argument("--interval", default="5minute"); p.add_argument("--prev-close", type=float)
    p.add_argument("--out", default=KITE_DIR)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"kite-import": _cmd_kite_import, "clock": _cmd_clock, "costs": _cmd_costs, "symbol": _cmd_symbol, "holidays": _cmd_holidays,
                "instrument": _cmd_instrument}[a.cmd](a)
    except MarketError as e:
        print("market.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
