#!/usr/bin/env python3
"""Position sizing for trader-budget (NSE / Zerodha). Standard library only, no network.

Sizes ONE intraday trade from trader/plan.json (written by trader-exit) to your
budget and your max loss per trade, then writes one proposed order to
trader/orders.json: an MIS entry plus the stop-loss and target that go with it.
It never places anything: trader-risk checks the order and trader-broker asks
you yes or no.

    python3 size.py                                  # trader/plan.json + trader/config.json
    python3 size.py --write                          # write trader/orders.json
    python3 size.py --limit                          # a limit order at the plan's entry (the default)
    python3 size.py --limit 1432.50 --write          # a limit order at Rs 1,432.50
    python3 size.py --market                         # a market order instead (not recommended)
    python3 size.py --now "2026-10-26 10:15" --json  # pin the clock (India time)

How many shares. Whole shares only (the cash segment has no fractions).

    shares = floor of the smallest of
      max loss       max_loss_per_trade_inr / risk per share (entry minus stop)
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget minus the cash buffer) / entry
                     (and no more than the cash in trader/portfolio.json, if you have one)

MIS leverage is NOT used for sizing: you size against your own cash. Zerodha
may give you more buying power on MIS; this pack does not rely on it.

Every price is moved to the NSE tick (Rs 0.05): the entry to the nearest tick,
the stop DOWN and the target UP, so rounding never makes the trade look better
than it is. The report also shows the round-trip charges (brokerage, STT,
exchange fees, GST, stamp duty) and the move needed just to break even.

The entry it sizes at: a limit order's limit price; for a market order the
plan's entry, or the price in trader/prices.json if that is fresh (under 15
minutes old) and higher. Zero shares (the stop is too far for your max loss) is
a plain refusal: no order is written. So is a reward:risk under min_reward_risk,
or charges over max_cost_r of the risk.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-market"))
import market  # noqa: E402
from market import (to_local, tick_round, cost_rates, round_trip_costs, breakeven_move,  # noqa: E402
                    clean_symbol, valid_symbol)

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 200000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "max_cost_r": 0.5,
    "require_stop": True,
    "no_entries_before": market.DEFAULT_NO_ENTRIES_BEFORE,
    "no_entries_after": market.DEFAULT_NO_ENTRIES_AFTER,
    "flat_by": market.DEFAULT_FLAT_BY,
    "broker": "zerodha",
    "product": "MIS",
    "stop_limit_buffer_ticks": 4,
    "paper": True,
    "blocked": [],
    "allow_margin": False,
    "allow_short": False,
    "costs": {},
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "flag": "flag", "bounce": "oversold bounce"}


class SizeError(Exception):
    pass


def parse_now(text):
    return market.parse_now(text, error=SizeError)


def parse_utc(text):
    """"2026-10-26T04:52:13Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def price_time(doc, sym):
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, time_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- money helpers

CURRENT_TICK = [market.TICK]     # the tick of the stock being sized; set by size_trade and bracket_order


def tick(x, mode="nearest"):
    return float(tick_round(x, mode, CURRENT_TICK[0]))


def shares_for(inr, per_share):
    """Whole shares that fit in `inr` at `per_share`, in exact integer paise."""
    u, p = int(round(float(inr) * 100)), int(round(float(per_share) * 100))
    if p <= 0 or u <= 0:
        return 0
    return u // p


def _price(x):
    """1432.5 -> "1432.50": prices go to Kite as exact tick strings."""
    return "%.2f" % float(x)


def _num(x):
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def money(x):
    return market.money(x)


# ---------------------------------------------------------------- sizing

def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit  the limit price for a limit order (None = market)
    price  a FRESH latest price; a market order is sized at the higher of it and the plan's entry
    cash   the cash in trader/portfolio.json, if there is one
    today  India date (YYYY-MM-DD); a plan for another day is refused"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    sym_raw = str(plan.get("symbol") or "").strip()
    CURRENT_TICK[0] = market.tick_for(sym_raw) if sym_raw else market.TICK
    if not sym_raw:
        raise SizeError("the plan has no symbol (run trader-exit)")
    if not valid_symbol(sym_raw):
        raise SizeError("%r does not look like an NSE ticker" % sym_raw)
    sym = clean_symbol(sym_raw)
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) intraday trades only; the plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in India: make a new plan with trader-exit"
                        % (plan["date"], today))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit); an order with its exits needs "
                            "entry, stop and target" % name)
    blocked = {str(b).upper() for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")

    if limit is not None:
        ref, ref_from = tick(limit), "limit"
    elif price is not None and tick(price) > tick(entry_plan):
        ref, ref_from = tick(price), "price"
    else:
        ref, ref_from = tick(entry_plan), "plan"
    stop, target = tick(stop, "down"), tick(target, "up")
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (_price(stop), _price(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (_price(target), _price(ref)))
    risk_p = int(round((ref - stop) * 100))      # paise
    reward_p = int(round((target - ref) * 100))
    risk = risk_p / 100.0

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s cash buffer" % (money(budget), money(cash_buffer))
    if cash is not None and cash - cash_buffer < available - 0.005:
        available = cash - cash_buffer
        cash_note = "cash %s in trader/portfolio.json minus the %s buffer" % (money(cash), money(cash_buffer))
    available = max(round(available, 2), 0.0)

    limits = []
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    if max_loss:
        limits.append({"name": "max loss", "inr": max_loss, "per": risk, "shares": shares_for(max_loss, risk),
                       "how": "%s max loss / %s risk a share" % (money(max_loss), _price(risk))})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "inr": cap, "per": ref, "shares": shares_for(cap, ref),
                       "how": "%s%% of %s = %s / %s" % (_num(cap_pct), money(budget), money(cap), _price(ref))})
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "inr": max_order, "per": ref, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (money(max_order), _price(ref))})
    limits.append({"name": "cash", "inr": available, "per": ref, "shares": shares_for(available, ref),
                   "how": "%s = %s / %s" % (cash_note, money(available), _price(ref))})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    cost = round(qty * ref, 2)
    loss = round(qty * risk_p / 100.0, 2)

    rates = cost_rates(c)
    charges = round_trip_costs(ref, target, qty, rates) if qty else None
    charges_stop = round_trip_costs(ref, stop, qty, rates) if qty else None
    be = breakeven_move(ref, qty, rates) if qty else 0.0
    cost_r = round(charges["total"] / loss, 3) if (qty and loss) else None

    refusal = None
    if qty == 0:
        if bound[0] == "max loss":
            refusal = ("one share risks %s (entry %s minus stop %s), more than your %s max loss per trade. "
                       "No order. Skip this trade, or plan a closer stop with trader-exit."
                       % (money(risk), _price(ref), _price(stop), money(max_loss)))
        else:
            refusal = ("one share of %s costs %s, more than your %s limit (%s). No order."
                       % (sym, money(ref), bound[0], limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif _f(c.get("min_reward_risk")) and reward_p < _f(c["min_reward_risk"]) * risk_p - EPS:
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so trader-risk "
                   "would block it. No order. Skip it, or make a new plan with trader-exit."
                   % (reward_p / float(risk_p), _price(target), _price(ref), _price(stop), _num(c["min_reward_risk"])))
    elif _f(c.get("max_cost_r")) and cost_r is not None and cost_r > _f(c["max_cost_r"]) + EPS:
        refusal = ("the round-trip charges (%s) are %.2fR of the %s you risk, over your %sR cap: the trade must "
                   "earn the charges back before it earns anything. No order. A wider target or a bigger "
                   "stop distance in rupees helps; a tiny risk per share does not."
                   % (money(charges["total"]), cost_r, money(loss), _num(c["max_cost_r"])))
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = ("%d share%s of %s is %s, under your %s minimum order. No order."
                   % (qty, "" if qty == 1 else "s", sym, money(cost), money(c["min_order_inr"])))
    return {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "entry_time": plan.get("entry_time", ""), "entry_plan": tick(entry_plan),
        "entry": ref, "entry_from": ref_from, "stop": stop, "target": target,
        "risk_per_share": risk, "reward_risk": round(reward_p / float(risk_p), 2),
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_inr": max_loss, "limits": limits, "qty": qty, "bound_by": bound[0],
        "cost": cost, "max_loss": loss, "flat_by": plan.get("flat_by") or c.get("flat_by"),
        "order_type": "limit" if limit is not None else "market",
        "charges_at_target": charges["total"] if charges else None,
        "charges_at_stop": charges_stop["total"] if charges_stop else None,
        "charges_parts": charges, "breakeven_move": be, "cost_r": cost_r,
        "product": str(c.get("product") or "MIS").upper(),
        "stop_limit_buffer_ticks": int(c.get("stop_limit_buffer_ticks") or 0),
        "refusal": refusal,
    }


def bracket_order(s):
    CURRENT_TICK[0] = market.tick_for(s["symbol"])
    """The one proposed order (strings, Kite-shaped) plus why and plan.

    Kite has no bracket order. The entry is placed first; when it fills, the stop-loss
    (SL, trigger plus a limit a few ticks lower) and the target (LIMIT sell) are placed
    as two orders and the other is cancelled when one fills. trader-broker walks through it."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"symbol": s["symbol"], "exchange": "NSE", "side": "buy", "qty": str(int(s["qty"])),
         "type": s["order_type"], "time_in_force": "day", "product": s["product"]}
    if s["order_type"] == "limit":
        o["limit_price"] = _price(s["entry"])
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "day trade")
    why = ("%s on %s: %d shares, bound by your %s (%d x %s = %s at the stop)"
           % (setup, s["symbol"], s["qty"],
              {"max loss": "%s max loss per trade" % money(s["max_loss_per_trade_inr"] or 0),
               "position cap": "position cap", "max order": "max order",
               "cash": "cash after the buffer"}[s["bound_by"]],
              s["qty"], _price(s["risk_per_share"]), money(s["max_loss"])))
    sl_limit = tick(s["stop"] - s["stop_limit_buffer_ticks"] * float(CURRENT_TICK[0]), "down")
    o.update({
        "order_class": "bracket",
        "take_profit_limit_price": _price(s["target"]),
        "stop_loss_trigger_price": _price(s["stop"]),
        "stop_loss_limit_price": _price(sl_limit),
        "why": why,
        "plan": {"entry": s["entry"], "stop": s["stop"], "target": s["target"],
                 "risk_per_share": s["risk_per_share"], "max_loss": s["max_loss"], "setup": s["setup"],
                 "entry_time": s["entry_time"], "flat_by": s["flat_by"]},
    })
    return o


def plan_report(s, order, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append("%s buy, %s, entry %s%s" % (s["symbol"], setup, _price(s["entry_plan"]),
                                            (" at " + s["entry_time"]) if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (_price(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             _price(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in notes:
        lines.append(n)
    src = {"plan": "the plan's entry", "price": "the latest price, above the plan's entry",
           "limit": "your limit price"}[s["entry_from"]]
    lines.append("Sized at %s (%s). Risk %s a share (entry %s minus stop %s). Reward:risk %.2f."
                 % (_price(s["entry"]), src, _price(s["risk_per_share"]), _price(s["entry"]), _price(s["stop"]),
                    s["reward_risk"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        lines.append("  %-13s %-62s %4d shares%s" % (l["name"], l["how"], l["shares"],
                                                      "  <- binds" if l["binds"] else ""))
    lines.append("")
    if s["qty"] and s["charges_at_target"] is not None:
        lines.append("Charges (round trip, Zerodha rates): about %s if the target fills, %s if the stop does. "
                     "That is %.2fR. The price must rise %s a share just to break even."
                     % (money(s["charges_at_target"]), money(s["charges_at_stop"]), s["cost_r"] or 0,
                        _price(s["breakeven_move"])))
        lines.append("")
    if order is None:
        lines.append("No order: " + s["refusal"])
        lines.append("Education and tooling, not financial advice.")
        return "\n".join(lines)
    lines.append("Size: %d shares of %s = %s at %s. Max loss at the stop: %d x %s = %s (before charges)."
                 % (s["qty"], s["symbol"], money(s["cost"]), _price(s["entry"]), s["qty"],
                    _price(s["risk_per_share"]), money(s["max_loss"])))
    lines.append("A stop is an order, not a promise: in a fast market, or if the stock hits its circuit, it can "
                 "fill below %s or not at all." % _price(s["stop"]))
    lines.append("")
    lines.append("Proposed order (nothing has been placed):")
    how = "limit %s" % order["limit_price"] if order["type"] == "limit" else "market"
    lines.append("  buy %s %s (NSE), %s, %s, day" % (order["qty"], order["symbol"], how, order["product"]))
    lines.append("  then stop-loss SELL: trigger %s, limit %s" % (order["stop_loss_trigger_price"],
                                                                 order["stop_loss_limit_price"]))
    lines.append("  and target SELL: limit %s (when one exit fills, cancel the other)"
                 % order["take_profit_limit_price"])
    if s["flat_by"]:
        lines.append("Flat by %s IST: anything still open then gets closed by you, before Zerodha's own "
                     "auto square-off (15:25, and a Rs 50 + GST fee per order)." % s["flat_by"])
    lines.append("")
    lines.append("Next: trader-risk checks it, then trader-broker asks you yes or no.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": utc_iso(now_utc),
        "status": "proposed",
        "broker": "zerodha",
        "paper": bool(cfg.get("paper", True)),
        "note": ("Proposals only. Nothing is placed until trader-risk passes each order "
                 "and you say yes to it in the chat."),
        "source": source,
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_local(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first)")
    limit = None
    if not a.market:
        if a.limit in (None, "plan"):
            limit = _f((plan or {}).get("entry"))
        else:
            limit = _f(a.limit)
        if limit is None or limit <= 0:
            raise SizeError("--limit wants a price, like --limit 1432.50")
    sym = str((plan or {}).get("symbol") or "").upper()
    try:
        sym = clean_symbol(sym)
    except market.MarketError:
        pass
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and limit is None and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        if p is not None and fresh:
            price = p
            if tick(p) > tick(_f(plan.get("entry")) or 0):
                notes.append("%s last %s (as of %s IST) is above the plan's entry %s, so the risk "
                             "is bigger: sized at %s." % (sym, _price(p), to_local(t).strftime("%H:%M"),
                                                          _price(plan.get("entry")), _price(p)))
        elif p is not None:
            notes.append("%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                         % (sym, ppath, ("from " + to_local(t).strftime("%Y-%m-%d %H:%M") + " IST")
                            if t else "undated", FRESH_MINUTES))
    cash = None
    if a.portfolio and os.path.exists(a.portfolio):
        pf = read_json(a.portfolio, "portfolio")
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
    s = size_trade(plan, cfg, a.budget, limit, price, cash, today)
    order = bracket_order(s)
    if order and a.write:
        src = "trader/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        write_orders(a.write, [order], cfg, src, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2,
                         ensure_ascii=False))
    else:
        print(plan_report(s, order, notes))
        if order and a.write:
            print("Wrote 1 proposed order to %s" % a.write)
    return 0 if order else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size an intraday trade to your budget and max loss (trader-budget).")
    ap.add_argument("--plan", default="trader/plan.json", help="the trade plan (default trader/plan.json)")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget in the config (rupees)")
    ap.add_argument("--limit", nargs="?", const="plan", metavar="PRICE",
                    help="a limit order at the plan's entry (default) or at PRICE")
    ap.add_argument("--market", action="store_true", help="a market order instead of a limit order")
    ap.add_argument("--prices", help="prices JSON (default trader/prices.json if it exists)")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; its cash caps the size if it exists")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-26 10:15\"")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed order (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        return run_plan(a, cfg)
    except (SizeError, market.MarketError) as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
