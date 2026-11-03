#!/usr/bin/env python3
"""News monitor for trader-news (India). Standard library only.

Watches headlines and NSE corporate announcements for the stocks you watch or hold and
prints only what is new since the last check, newest first, with how far each
stock has moved today and a flag on anything that touches your tickers.

    python3 news.py                                  # watchlist + plan + portfolio tickers
    python3 news.py --tickers RELIANCE INFY "M&M"
    python3 news.py --tickers RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-filings                     # headlines only
    python3 news.py --no-moves                       # skip the price line per ticker

Your tickers, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources, one request each per ticker:
  - Yahoo Finance headline RSS (India edition):
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>.NS&region=IN&lang=en-IN
  - NSE corporate announcements, the exchange's own filings feed (financial results,
    board-meeting outcomes, acquisitions, dividends, management changes, insider and
    SAST disclosures, credit ratings, trading-window notices):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<TICKER>
    NSE's site wants browser-like headers and a session cookie, which this tool
    gets by visiting nseindia.com first. NSE does turn scripts away at times (HTTP
    401/403): the tool says so and the headlines still run. It is the India
    counterpart of the US tool's SEC 8-K feed; the announcement's own category
    ("Financial Results", "Outcome of Board Meeting", ...) is its subject line.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<TICKER>.NS?range=1d&interval=5m
    A move close to a 2 / 5 / 10 / 20 % price band is flagged: the share may be at
    its circuit limit and stuck.

Times print in India time (IST). State: trader/state/news.json remembers
what you have seen (30 days), so an item shows once. Offline: --offline DIR
reads yahoo-<TICKER>.xml, chart-<TICKER>.json and nse-<TICKER>.json from DIR.
The tests use it.

Fundamentals, analyst targets and "how is the company doing" are not here: use
Tickertape (or Screener) for those, then come back for the day's news.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import http.cookiejar

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "trader-market"))
import market  # noqa: E402  (the India profile: clock, holidays, tick, symbols, costs)
from market import (NSE_HOLIDAYS, NSE_EARLY_CLOSE, CALENDAR_YEARS, OPEN, CLOSE, EARLY,  # noqa: E402,F401
                    to_local, local_to_utc)

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_HOME = "https://www.nseindia.com/"
NSE_ANN = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving

# NSE announcement categories (the "desc" field) that usually move a share, flagged with "!".
IMPORTANT_DESC = re.compile(
    r"financial results|outcome of board meeting|acquisition|amalgamation|scheme of arrangement|"
    r"change in directors|change in management|resignation|fraud|default|insolvency|"
    r"credit rating|delisting|open offer|bonus|buy.?back|dividend|stock split|sub.?division|"
    r"preferential|rights issue|qip|trading halt|suspension|loss of|search|seizure", re.I)
KEYWORDS = [
    ("earnings", r"\bresults?\b|\bQ[1-4]\b|\bquarterly\b|\bnet profit\b|\bPAT\b|\bEBITDA\b|\bguidance\b|\bearnings\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\bbrokerage\b|\boutperform\b|"
                r"\bunderperform\b|\b(?:Jefferies|Motilal|Nomura|CLSA|Macquarie|Goldman|Kotak|Axis Securities)\b"),
    ("legal", r"\bSEBI\b|\bNCLT\b|\bcourt\b|\bprobe\b|\binvestigation\b|\bshow.?cause\b|\bpenalty\b|"
              r"\btax notice\b|\bGST notice\b|\bED\b|\bCBI\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bbuyout\b|\btakeover\b|\bopen offer\b|"
             r"\bstake sale\b|\bblock deal\b|\bbulk deal\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus\b|\bstock split\b|\brights issue\b|\bQIP\b|\bOFS\b|"
                r"\bpreferential\b|\bIPO\b|\bwarrants?\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\bdefault\b|\bNPA\b|\bfraud\b|\bbankrupt|\binsolvency\b|\bpledge[ds]?\b|\bdelist|\bhack|\bbreach\b"),
    ("halt", r"\bcircuit\b|\btrading halt\b|\bsuspend|\bASM\b|\bGSM\b|\bsurveillance\b|\bT2T\b"),
    ("regulatory", r"\bRBI\b|\bCCI\b|\bDGCA\b|\bIRDAI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b"),
    ("macro", r"\brepo rate\b|\bRBI policy\b|\bFed\b|\bCPI\b|\binflation\b|\bGDP\b|\bFII\b|\bDII\b|\bbudget\b|"
              r"\bSensex\b|\bNifty\b|\bcrude\b|\brupee\b|\btariffs?\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_local(utc):
    """Aware or naive UTC -> naive India wall-clock time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + market.UTC_OFFSET


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _clean(sym):
    try:
        return market.clean_symbol(sym)
    except market.MarketError:
        return str(sym).upper()


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[_clean(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(_clean(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[_clean(sym)] = p.get("name") or out.get(_clean(sym))
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._opener = None          # NSE session (cookies)

    def _throttle(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def get(self, url, name, nse=False):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        self._throttle()
        headers = {"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"}
        if nse:
            return self._nse(url, headers)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return self._body(resp)
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))

    @staticmethod
    def _body(resp):
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data

    def _nse(self, url, headers):
        """NSE's API only answers a visitor with its cookies: load the home page once, then ask."""
        if self._opener is None:
            self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                self._opener.open(urllib.request.Request(NSE_HOME, headers=dict(headers, Accept="text/html,*/*")),
                                  timeout=30).read()
            except (urllib.error.URLError, OSError) as exc:
                raise NewsError("could not reach nseindia.com: %s" % getattr(exc, "reason", exc))
        req = urllib.request.Request(url, headers=dict(headers, Accept="application/json, text/plain, */*",
                                                       Referer=NSE_HOME + "get-quotes/equity"))
        try:
            with self._opener.open(req, timeout=30) as resp:
                return self._body(resp)
        except urllib.error.HTTPError as exc:
            raise NewsError("NSE answered HTTP %s (it turns scripts away at times; open nseindia.com "
                            "announcements in a browser for this one)" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise NewsError("could not reach nseindia.com: %s" % getattr(exc, "reason", exc))


def yahoo_symbol(ticker):
    return market.yahoo_symbol(ticker)


def yahoo_path(ticker):
    return market.yahoo_path_symbol(ticker)


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 (Atom) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_nse_time(value):
    """NSE writes announcement times as '24-Oct-2026 14:05:33' (India time) -> aware UTC."""
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
        try:
            local = _dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
        return (local - market.UTC_OFFSET).replace(tzinfo=_dt.timezone.utc)
    return None


def parse_nse_announcements(data, ticker):
    """NSE corporate-announcements JSON -> items. The category is the headline; the text is the summary."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE announcements for %s are not JSON" % ticker)
    rows = doc if isinstance(doc, list) else (doc.get("data") if isinstance(doc, dict) else None)
    if rows is None:
        raise NewsError("NSE announcements for %s are not in the shape this tool knows" % ticker)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cat = " ".join(str(r.get("desc") or "").split())
        text = " ".join(str(r.get("attchmntText") or r.get("sm_name") or "").split())
        stamp = r.get("an_dt") or r.get("sort_date") or r.get("exchdisstime") or ""
        ident = str(r.get("seq_id") or "") or hashlib.sha1(("%s|%s|%s" % (ticker, stamp, cat)).encode()).hexdigest()[:12]
        name = " ".join(str(r.get("sm_name") or "").split())
        title = "%s: %s" % (name or ticker, cat or "announcement")
        out.append({"id": "nse:" + ident, "source": "NSE filing", "kind": "filing", "form": cat,
                    "ticker": ticker, "title": title, "summary": text,
                    "important": bool(IMPORTANT_DESC.search(cat + " " + text[:200])),
                    "link": str(r.get("attchmntFile") or ""), "time": parse_nse_time(str(stamp))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_path(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_path(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(t),
                r"\b%s\.(?:NS|BO)\b" % re.escape(t)]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "filing" and re.search(r"financial results", it.get("form") or "", re.I) \
                and "earnings" not in it["tags"]:
            it["tags"].insert(0, "earnings")
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

HARD_TAGS = {"earnings", "deal", "capital", "people", "risk", "legal", "halt", "regulatory"}


def corroborate(items, status=None, hours=48):
    """Cross-check the two sources. A press headline about a hard event (results, deal, dividend, people, risk,
    legal, halt, regulatory) is CONFIRMED when NSE has a filing on the same stock, within `hours`, with a shared tag.
    A headline with no such filing is PRESS ONLY (or 'NSE not checked' when the exchange feed failed).
    A filing nobody wrote about is EXCHANGE ONLY: official, but not yet in the press."""
    status = status or {}
    filings = [i for i in items if i["kind"] == "filing"]
    for it in items:
        it["verdict"] = None
    used = set()
    for h in (i for i in items if i["kind"] == "headline"):
        hard = HARD_TAGS & set(h.get("tags") or [])
        if not hard:
            continue
        match = None
        for f in filings:
            if f["ticker"] != h["ticker"] or not (HARD_TAGS & set(f.get("tags") or []) & hard):
                continue
            if h["time"] and f["time"] and abs((h["time"] - f["time"]).total_seconds()) > hours * 3600:
                continue
            match = f
            break
        if match:
            h["verdict"] = "CONFIRMED by NSE filing"
            match["verdict"] = "also in the press"
            used.add(match["id"])
        elif status.get(h["ticker"], {}).get("nse"):
            h["verdict"] = "PRESS ONLY: no matching NSE filing yet, treat as unconfirmed"
        else:
            h["verdict"] = "NSE feed not checked: cannot confirm"
    for f in filings:
        if f["id"] not in used and f.get("verdict") is None:
            f["verdict"] = "EXCHANGE ONLY: official, not yet in the press"
    return items


def agreement(items):
    c = {"confirmed": 0, "press_only": 0, "exchange_only": 0}
    for it in items:
        v = it.get("verdict") or ""
        if v.startswith("CONFIRMED"):
            c["confirmed"] += 1
        elif v.startswith("PRESS ONLY"):
            c["press_only"] += 1
        elif v.startswith("EXCHANGE ONLY"):
            c["exchange_only"] += 1
    return c


def collect(fetcher, watch, state, filings=True, errors=None, status=None):
    errors = [] if errors is None else errors
    status = {} if status is None else status
    items = []
    for t in sorted(watch):
        try:
            items += parse_rss(fetcher.get(YAHOO % yahoo_path(t), "yahoo-%s.xml" % t), t)
            status.setdefault(t, {})["yahoo"] = True
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if filings:
        for t in sorted(watch):
            try:
                data = fetcher.get(NSE_ANN % urllib.request.quote(t, safe=""), "nse-%s.json" % t, nse=True)
                items += parse_nse_announcements(data, t)
                status.setdefault(t, {})["nse"] = True
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (t, exc))
                if "answered HTTP" in str(exc) or "could not reach" in str(exc):
                    break               # the session is refused: do not hammer it ticker by ticker
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        band = market.near_circuit(m["pct"])
        if band:
            flag = (flag + " " if flag else "") + "NEAR %d%% CIRCUIT" % band
        when = (" at %s IST" % to_local(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-12s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["ticker"], market.money(m["price"]), m["pct"], market.money(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_local(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        if it.get("verdict"):
            out.append("      >> " + it["verdict"])
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    ag = agreement(items)
    if any(ag.values()):
        out.append("Cross-check: %d confirmed by both Yahoo and NSE, %d press only (unconfirmed), %d exchange only (official)."
                   % (ag["confirmed"], ag["press_only"], ag["exchange_only"]))
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_local(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-filings", "--no-sec", dest="no_filings", action="store_true", help="headlines only")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="India time like 2026-10-26T10:15 (or ...Z), for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {market.clean_symbol(t): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    if args.now:
        try:
            now = market.parse_now(args.now).replace(tzinfo=_dt.timezone.utc)
        except Exception as exc:
            print("news: --now wants an India time like \"2026-10-26 10:15\" (%s)" % exc, file=sys.stderr)
            return 2
    else:
        now = _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    status = {}
    items = collect(fetcher, watch, state, filings=not args.no_filings, errors=errors, status=status)
    items = corroborate(dedupe(tag(items, watch)), status)
    for t in sorted(watch):
        st = status.get(t, {})
        if not args.no_filings and st.get("yahoo") != st.get("nse"):
            errors.append("%s: only %s answered, so nothing for it is cross-checked" % (t, "Yahoo" if st.get("yahoo") else "NSE" if st.get("nse") else "neither source"))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
