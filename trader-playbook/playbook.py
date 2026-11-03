#!/usr/bin/env python3
"""Playbook for trader-playbook: which steps to run, in what order, and what is already done.

    python3 playbook.py steps intraday --symbol RELIANCE     # ordered steps with done / next / not built
    python3 playbook.py horizons                              # what each horizon supports today

It reads the trader/ folder only. It runs nothing, places nothing and needs no network.
Standard library only.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-market"))
import market  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# (skill, command hint, check key or None, built?)
INTRADAY = [
    ("trader-market", "market.py clock", None, True),
    ("trader-market", "market.py instrument add <kite search json>", "instrument", True),
    ("trader-scanner", "scan.py daily | orb", None, True),
    ("trader-research", "record facts.json (Tickertape)", "research", True),
    ("trader-news", "news.py --tickers SYM", None, True),
    ("trader-candles", "candles.py scan bars.json", None, True),
    ("trader-entry", "entry.py check SYM --setup orb --write", "entry", True),
    ("trader-exit", "exit.py plan --write", "plan", True),
    ("trader-timemachine", "timemachine.py run --write", "timemachine", True),
    ("trader-budget", "size.py --write", "orders", True),
    ("trader-risk", "check.py", None, True),
    ("trader-broker", "ticket.py show | approve", None, True),
    ("trader-alerts", "alerts.py add --from-plan", None, True),
]
HORIZONS = {
    "intraday": {
        "summary": "Buy and sell the same day (MIS), cash segment. The full pipeline is built and tested offline.",
        "steps": INTRADAY,
    },
    "swing": {
        "summary": "Hold days to weeks. Technicals decide entry, stop and targets (trader-swing); events and weak markets only "
                   "shrink the size. Orders are placed by hand in Kite (stop-loss or GTT): trader-broker is same-day only.",
        "steps": [
            ("trader-swing", "swing.py scan bars.json --symbol SYM --max-loss N", "swing", True),
            ("trader-research", "record facts.json (red flags, results dates)", "research", True),
            ("trader-candles", "candles.py scan <daily bars> --timeframe day", None, True),
        ],
    },
    "longterm": {
        "summary": "Hold years. Fundamentals decide whether to own it; weekly/monthly technicals decide the price "
                   "(trader-longterm). Short-term news and mood are ignored on purpose. Research read only, no orders.",
        "steps": [
            ("trader-longterm", "longterm.py read facts.json bars.json", "longterm", True),
            ("portfolio and allocation (Kite holdings)", "", None, False),
        ],
    },
    "fno": {
        "summary": "Futures and options. Research only (trader-fno): levels, headwinds, Greeks, time decay, payoff, option-chain read. "
                   "No orders: trader-risk blocks F&O on purpose.",
        "steps": [
            ("trader-fno", "fno.py view bars.json --facts facts.json", None, True),
            ("trader-fno", "fno.py greeks | decay | payoff | chain", None, True),
            ("F&O margin, order and risk rules", "", None, False),
        ],
    },
}


def jload(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def status(key, symbol, today, folder):
    """True (done), False (not yet), or None (cannot tell)."""
    if key is None:
        return None
    if key == "instrument":
        return bool(symbol and market.tick_known(symbol)) if symbol else None
    if key in ("longterm", "swing"):
        doc = jload(os.path.join(folder, key + ".json")) or {}
        rec = (doc.get("stocks") or {}).get((symbol or "").upper())
        return bool(rec) if symbol else None
    if key == "research":
        doc = jload(os.path.join(folder, "research.json")) or {}
        rec = (doc.get("stocks") or {}).get((symbol or "").upper())
        return bool(rec and rec.get("date") == today) if symbol else None
    f = {"entry": "entry.json", "plan": "plan.json", "timemachine": "timemachine.json", "orders": "orders.json"}[key]
    doc = jload(os.path.join(folder, f))
    if doc is None:
        return False
    if not symbol:
        return True
    text = json.dumps(doc)
    return symbol.upper() in text


def cmd_steps(a):
    h = HORIZONS[a.horizon]
    now = market.parse_now(a.now) if a.now else market.utc_now()
    today = market.to_local(now).date().isoformat()
    sym = None
    if a.symbol:
        sym = market.clean_symbol(a.symbol)
    print("%s: %s" % (a.horizon.upper(), h["summary"]))
    if sym:
        print("Stock: %s   Today: %s" % (sym, today))
    nxt = False
    for i, (skill, cmd, key, built) in enumerate(h["steps"], 1):
        if not built:
            mark = "NOT BUILT"
        else:
            st = status(key, sym, today, a.folder)
            if st is True:
                mark = "done"
            elif st is False and not nxt:
                mark, nxt = "NEXT", True
            elif st is False:
                mark = "todo"
            else:
                mark = "-"
        print("%2d. [%-9s] %s%s" % (i, mark, skill, ("  " + cmd) if cmd else ""))
    print("\nEach step is advice or a calculation; only trader-broker can place an order, and only with the user's yes.")
    return 0


def cmd_horizons(a):
    for k, h in HORIZONS.items():
        print("%-9s %s" % (k, h["summary"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trading playbook (trader-playbook).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("steps")
    p.add_argument("horizon", choices=list(HORIZONS))
    p.add_argument("--symbol")
    p.add_argument("--folder", default="trader")
    p.add_argument("--now")
    sub.add_parser("horizons")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"steps": cmd_steps, "horizons": cmd_horizons}[a.cmd](a)
    except market.MarketError as e:
        print("playbook.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
