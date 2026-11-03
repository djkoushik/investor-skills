#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker (Zerodha Kite). Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket plus the
            exact arguments Claude will send to the Kite connector's
            place_order tool. An order is READY only if trader-risk passed THIS
            exact order today (matched by fingerprint) and nobody has decided
            on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  automate  on|off|status: automation is OFF (manual) unless the user turned it on
            today in their own words; it resets every day.
  auto-approve  approves ONE order without a typed yes, only under today's
            automation, a risk PASS, and (entries) a CLEAR research verdict.
  result    records what happened after the Allow once / Deny card:
            placed (with the Kite order id), denied, or failed.
  exits     after the entry FILLS: the two protective orders (stop-loss SL and
            target LIMIT) to place for the shares you actually got. Kite has
            no bracket order, so these are separate orders and you cancel the
            other one when one fills.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 2610260000123
    python3 ticket.py exits --index 1 --qty 52          # after the entry fills
    python3 ticket.py fill --symbol RELIANCE --side buy --qty 52 --price 1432.50
    python3 ticket.py flatten --symbol RELIANCE --qty 52
    python3 ticket.py log

Everything is written to trader/state/broker.json (append only). Dates are
India dates; --now "2026-10-26 10:15" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-market"))
import market  # noqa: E402
from market import to_local  # noqa: E402

# the same list as ORDER_FIELDS in trader-risk/check.py: the fingerprint must match
ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "type", "time_in_force", "product", "limit_price",
                "stop_price", "client_order_id", "order_class", "take_profit_limit_price",
                "stop_loss_trigger_price", "stop_loss_limit_price")
TIF_WORDS = {"day": "good for today", "ioc": "immediate or cancel"}
KITE_TYPE = {"market": "MARKET", "limit": "LIMIT", "stop": "SL-M", "stop_limit": "SL"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}


AUTO_WORDS = ("automate", "automatic", "automatically", "on your own")
AUTO_SCOPES = ("entry_exit", "entry", "exit")


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (India, from trader-market)

def parse_now(text):
    return market.parse_now(text, error=TicketError)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-26T10:15:00+05:30" (India wall clock with its offset)."""
    return to_local(now_utc).replace(microsecond=0).isoformat() + "+05:30"


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _tag(order):
    fp = fingerprint(order)
    return "dts" + fp[:12]


def _num(v):
    f = float(v)
    return int(f) if f == int(f) else round(f, 2)


def tool_input(order):
    """The exact arguments for the Kite connector's place_order tool (the entry).
    "why" and "plan" are ours and are never sent."""
    typ = str(order.get("type", "market")).lower()
    out = {"variety": "regular", "exchange": str(order.get("exchange") or "NSE").upper(),
           "tradingsymbol": str(order.get("symbol", "")).upper(),
           "transaction_type": str(order.get("side", "")).upper(),
           "quantity": int(_f(order.get("qty")) or 0),
           "product": str(order.get("product") or "MIS").upper(),
           "order_type": KITE_TYPE.get(typ, typ.upper()),
           "validity": str(order.get("time_in_force", "day")).upper()}
    if typ in ("limit", "stop_limit") and order.get("limit_price") is not None:
        out["price"] = _num(order["limit_price"])
    if typ in ("stop", "stop_limit") and order.get("stop_price") is not None:
        out["trigger_price"] = _num(order["stop_price"])
    out["tag"] = _tag(order)
    return out


def exit_inputs(order, qty=None):
    """After the entry fills: the stop-loss (SL: trigger + limit) and target (LIMIT) SELL orders
    for the shares actually bought. None when the order has no exits."""
    if not is_bracket(order):
        return None
    q = int(_f(qty) if qty is not None else (_f(order.get("qty")) or 0))
    base = {"variety": "regular", "exchange": str(order.get("exchange") or "NSE").upper(),
            "tradingsymbol": str(order.get("symbol", "")).upper(), "transaction_type": "SELL",
            "quantity": q, "product": str(order.get("product") or "MIS").upper(), "validity": "DAY"}
    stop, target = dict(base), dict(base)
    trig = order.get("stop_loss_trigger_price")
    lim = order.get("stop_loss_limit_price")
    if trig is not None:
        if lim is not None:
            stop.update({"order_type": "SL", "trigger_price": _num(trig), "price": _num(lim)})
        else:
            stop.update({"order_type": "SL-M", "trigger_price": _num(trig)})
        stop["tag"] = "dts" + fingerprint(order)[:11] + "S"
    else:
        stop = None
    if order.get("take_profit_limit_price") is not None:
        target.update({"order_type": "LIMIT", "price": _num(order["take_profit_limit_price"])})
        target["tag"] = "dts" + fingerprint(order)[:11] + "T"
    else:
        target = None
    return {"stop_loss": stop, "target": target}


def _money(v):
    return market.money(v)


def _p(v):
    return "%.2f" % float(v)


def is_bracket(order):
    oc = str(order.get("order_class") or "").lower()
    legs = order.get("take_profit_limit_price") is not None or order.get("stop_loss_trigger_price") is not None
    return oc == "bracket" or (not oc and legs)


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, or (None, stop)."""
    stop = _f(order.get("stop_loss_trigger_price")) or _f((order.get("plan") or {}).get("stop"))
    q = _f(order.get("qty"))
    if not stop or not q:
        return None, stop
    plan = order.get("plan") or {}
    if str(order.get("type", "market")) == "limit" and _f(order.get("limit_price")):
        entry = _f(order["limit_price"])
    elif _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0, 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("side", "")).upper()
    sym = order.get("symbol", "?")
    typ = str(order.get("type", "market"))
    tif = TIF_WORDS.get(str(order.get("time_in_force", "day")), order.get("time_in_force"))
    prod = str(order.get("product") or "MIS").upper()
    if is_bracket(order):
        how = "limit %s" % _p(order["limit_price"]) if typ == "limit" and order.get("limit_price") else typ
        parts = ["%s %s %s (%s), %s, %s, %s" % (side, _str_num(order.get("qty")), sym,
                                               str(order.get("exchange") or "NSE").upper(), how, prod, tif)]
        if order.get("stop_loss_trigger_price") is not None:
            leg = "then stop-loss SELL, trigger %s" % _p(order["stop_loss_trigger_price"])
            if order.get("stop_loss_limit_price") is not None:
                leg += " (limit %s)" % _p(order["stop_loss_limit_price"])
            parts.append(leg)
        if order.get("take_profit_limit_price") is not None:
            parts.append("and target SELL, limit %s" % _p(order["take_profit_limit_price"]))
        loss, stop = max_loss(order)
        if loss is not None:
            parts.append("max loss about %s if the stop fills at %s (it can fill worse in a fast market, "
                         "or not at all at a circuit)" % (_money(loss), _p(stop)))
        return parts
    q = _str_num(order.get("qty"))
    what = "%s share%s of %s (%s)" % (q, "" if q == "1" else "s", sym, prod)
    if typ == "limit":
        how = "limit order at %s" % _money(order.get("limit_price"))
    elif typ == "stop":
        how = "stop order at %s" % _money(order.get("stop_price"))
    elif typ == "stop_limit":
        how = "stop %s, limit %s" % (_money(order.get("stop_price")), _money(order.get("limit_price")))
    else:
        how = "%s order" % typ.replace("_", " ")
    return ["%s %s, %s, %s" % (side, what, how, tif)]


def plain(order):
    """BUY 52 RELIANCE (NSE), limit 1432.50, MIS, good for today / then stop-loss SELL, trigger 1424.10 ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("qty", "limit_price", "stop_loss_trigger_price", "take_profit_limit_price",
              "stop_loss_limit_price"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z.\-]{0,6}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the place_order call" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


# ---------------------------------------------------------------- automation (opt-in, one day at a time)

def auto_state(path):
    return read_json(path, "automation state", required=False) or {}


def auto_active(doc, today):
    """(active, why). Automation is on only if the user turned it on TODAY; it resets to manual each day."""
    if not doc.get("enabled"):
        return False, "automation is off (manual mode)"
    if doc.get("date") != today:
        return False, "automation was turned on for %s, not today; it resets to manual every day" % doc.get("date")
    return True, "automation is on for today (%s, scope %s)" % ("practice mode" if doc.get("paper", True) else "LIVE", doc.get("scope"))


def research_clear(path, symbol, today, allow_caution=False):
    """(ok, why): today's trader-research verdict must be CLEAR (or CAUTION if the config allows it)."""
    doc = read_json(path, "research", required=False) or {}
    rec = (doc.get("stocks") or {}).get(symbol)
    if not rec or rec.get("date") != today:
        return False, "no trader-research verdict for %s today (run /trader-research)" % symbol
    v = rec.get("verdict")
    if v == "CLEAR" or (v == "CAUTION" and allow_caution):
        return True, "research %s" % v
    return False, "research says %s for %s: automation does not trade it" % (v, symbol)


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    paper = cfg.get("paper", True)
    acct = "PRACTICE MODE (Claude will not place it)" if paper else "LIVE ACCOUNT, REAL MONEY"
    lines = ["ORDER %d of %d, %s, Zerodha Kite" % (idx, total, acct)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    lines.append("  Claude will call the Kite connector's place_order with exactly:")
    for l in json.dumps(tool_input(order), indent=2).splitlines():
        lines.append("    " + l)
    if is_bracket(order):
        lines.append("  Kite has no bracket order. When this entry FILLS, ticket.py exits gives the stop-loss")
        lines.append("  and the target for the shares you got. They are two orders: when one fills, cancel")
        lines.append("  the other (cancel_order). Zerodha squares off any open MIS position itself around")
        lines.append("  15:25 and charges Rs 50 + GST an order: close it yourself by flat_by.")
    if ready and paper:
        lines.append("  Practice mode (\"paper\": true): nothing is sent. Set \"paper\": false in trader/config.json")
        lines.append("  to place real orders.")
    if ready:
        lines.append("  Reply \"yes\" to place this one order, or \"no\" to skip it.")
        lines.append("  Claude's own card then asks again: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "zerodha"), "tool": "place_order",
            "tool_input": tool_input(order), "exits": exit_inputs(order), "why": order.get("why", ""),
            "bracket": is_bracket(order), "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log, so trader-risk can count day trades (a buy and a sell the same day)."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_local(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-26")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    on, why = auto_active(auto_state(a.auto), today)
    print("\nMODE: %s (%s)." % ("AUTOMATED" if on else "MANUAL", why))
    print("One order at a time: in manual mode a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "qty": order.get("qty"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker", "zerodha")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if cfg.get("paper", True):
        print("Practice mode (\"paper\": true): do NOT call place_order. Record it with: ticket.py result "
              "--index %d --status denied --note practice" % a.index)
    else:
        print("Now call the Kite connector's place_order with exactly:")
        print(json.dumps(tool_input(order), indent=2))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed --broker-id <order id>" % a.index)
    return 0


def cmd_automate(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now, today = _clock(a)
    doc = auto_state(a.auto)
    if a.action == "status":
        on, why = auto_active(doc, today)
        print("MODE: %s. %s." % ("AUTOMATED" if on else "MANUAL", why[0].upper() + why[1:]))
        n = len(set(doc.get("practice_days", [])))
        print("Practice days with automation so far: %d (live automation needs %d)." % (n, int(cfg.get("auto_practice_sessions", 5))))
        return 0 if on else 1
    if a.action == "off":
        doc.update({"enabled": False, "off_at": ist_iso(now)})
        write_json(a.auto, doc)
        print("Automation is OFF. Back to manual: you place every entry and exit yourself.")
        return 0
    reply = (a.reply or "").strip()
    words = set(re.findall(r"[a-z']+", reply.lower()))
    if not reply or not any(w in reply.lower() for w in AUTO_WORDS):
        print("Not turned on: only the user's own words count (for example \"automate today\" or \"decide on your own "
              "today\"). Ask the user; never turn this on for them.")
        return 1
    if "?" in reply or (DOUBT_WORDS & words):
        print("Not turned on: the reply is a question or has a condition in it. Ask again.")
        return 1
    paper = bool(cfg.get("paper", True))
    need = int(cfg.get("auto_practice_sessions", 5))
    have = len(set(doc.get("practice_days", [])))
    if not paper and have < need:
        print("Not turned on: live automation needs %d practice days first (you have %d). Run it with \"paper\": true "
              "until then." % (need, have))
        return 1
    scope = a.scope or cfg.get("auto_scope", "entry_exit")
    if scope not in AUTO_SCOPES:
        raise TicketError("scope must be one of %s" % ", ".join(AUTO_SCOPES))
    doc.update({"enabled": True, "date": today, "enabled_at": ist_iso(now), "reply": reply, "scope": scope, "paper": paper})
    write_json(a.auto, doc)
    print("Automation is ON for %s only (%s, scope %s). It switches back to manual tomorrow." %
          (today, "practice mode" if paper else "LIVE: real money", scope))
    print("Each order still needs a trader-risk PASS today and a CLEAR research verdict; it still stays inside your limits.")
    return 0


def cmd_auto_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    doc = auto_state(a.auto)
    on, why = auto_active(doc, today)
    if not on:
        raise TicketError("not automated: %s. Ask the user for a yes on this order instead." % why)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    kind = kind_of(order, rv)
    scope = doc.get("scope", "entry_exit")
    if (kind == "entry" and scope == "exit") or (kind == "exit" and scope == "entry"):
        raise TicketError("this is an %s and today's automation scope is %s: ask the user for a yes" % (kind, scope))
    sym = str(order.get("symbol", "")).upper()
    if kind == "entry":
        if str(order.get("type", "")).lower() != "limit":
            raise TicketError("automation only places LIMIT entries (this one is %s): ask the user" % order.get("type"))
        if not is_bracket(order):
            raise TicketError("automation never enters without a stop-loss and a target on the order: ask the user")
        ok, why = research_clear(a.research, sym, today, bool(cfg.get("auto_allow_caution", False)))
        if not ok:
            raise TicketError("%s. Ask the user, or skip it." % why)
        if bool(doc.get("paper", True)) != bool(cfg.get("paper", True)):
            raise TicketError("practice/live mode changed since automation was turned on; turn it on again")
    loss, stop = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order), "symbol": sym,
           "side": order.get("side"), "qty": order.get("qty"), "kind": kind, "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": "AUTO: " + doc.get("reply", ""), "auto": True,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker", "zerodha")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    if cfg.get("paper", True):
        days = set(doc.get("practice_days", [])); days.add(today)
        doc["practice_days"] = sorted(days)
        write_json(a.auto, doc)
    print("Auto-approved order %d under today's automation: %s." % (a.index, plain(order)))
    if cfg.get("paper", True):
        print("Practice mode (\"paper\": true): do NOT call place_order. Record it with: ticket.py result "
              "--index %d --status denied --note practice" % a.index)
    else:
        print("Now call the Kite connector's place_order with exactly:")
        print(json.dumps(tool_input(order), indent=2))
        print("Then: ticket.py result --index %d --status placed|denied|failed --broker-id <order id>" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
        if rec.get("kind") == "entry" and is_bracket(order):
            print("Check get_orders until the entry is COMPLETE, then: ticket.py exits --index %d --qty <filled>"
                  % a.index)
            print("Place the stop-loss FIRST, then the target. When one fills, cancel the other.")
    return 0


def cmd_exits(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = latest(state, fingerprint(order), today)
    if not rec or rec.get("status") not in ("placed",):
        raise TicketError("order %d has not been recorded as placed today: place the entry first" % a.index)
    legs = exit_inputs(order, a.qty)
    if not legs:
        raise TicketError("order %d has no stop-loss or target" % a.index)
    q = legs["stop_loss"]["quantity"] if legs["stop_loss"] else legs["target"]["quantity"]
    if q <= 0:
        raise TicketError("--qty wants the shares that filled, like --qty 52")
    if a.json:
        print(json.dumps(legs, indent=2))
        return 0
    print("The entry for %s filled %d shares. Protect them, stop-loss first (your yes for order %d covers these "
          "two exits; Claude's Allow once / Deny card still asks for each):" % (order.get("symbol"), q, a.index))
    for name in ("stop_loss", "target"):
        if legs[name]:
            print("\n%s:" % name.replace("_", "-"))
            print(json.dumps(legs[name], indent=2))
    print("\nThen watch both with get_orders. When one COMPLETES, cancel the other with cancel_order "
          "(variety regular). If the entry filled only partly, pass --qty with what filled. "
          "Close anything still open yourself by flat_by: Zerodha squares off MIS near 15:25 for Rs 50 + GST an order.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_local(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-26")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(),
                                                                _money(a.price), date, a.portfolio,
                                                                _money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    source = "the broker, get_positions"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0:
            raise TicketError("--qty wants the number of shares you hold, like --qty 52")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker (get_positions "
                              "%s) and pass --qty if you do hold some" % (sym, a.portfolio, sym))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or market.DEFAULT_FLAT_BY
    order = {"symbol": sym, "exchange": "NSE", "side": "sell", "qty": _str_num(qty), "type": "market",
             "time_in_force": "day", "product": "MIS",
             "why": "flat by %s IST: closes the %s day trade before Zerodha's auto square-off (qty from %s)"
             % (flat_by, sym, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_positions (%s) before you say yes." % (source, sym))
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. If the stop-loss or the target is still open, Kite holds those shares for it and rejects a new")
    print("     sell. Cancel them first: get_orders (open orders), then cancel_order for each exit leg")
    print("     (Needs approval). Then place_order with the ticket. A market sell is used here so that it")
    print("     fills; check the price first.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-26 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--auto", default="trader/state/automation.json")
        p.add_argument("--research", default="trader/research.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("automate", help="turn automation on for today, off, or show the mode (the user's words only)")
    p.add_argument("action", choices=["on", "off", "status"])
    p.add_argument("--reply", help="the user's words turning it on, word for word")
    p.add_argument("--scope", choices=list(AUTO_SCOPES))
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--auto", default="trader/state/automation.json")
    clock(p)
    p = sub.add_parser("auto-approve", help="approve one order under today's automation (no typed yes)")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the Allow once / Deny card")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("exits", help="after the entry fills: the stop-loss and target orders to place")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--qty", help="shares that filled (default: the order's qty)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from get_positions (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result, "exits": cmd_exits,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log, "automate": cmd_automate,
                "auto-approve": cmd_auto_approve}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
