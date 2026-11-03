#!/usr/bin/env python3
"""Research verdicts for trader-research. Standard library only, no network.

Claude gathers the facts from the Tickertape connector (see SKILL.md) and hands them to this
tool as one small JSON per stock. This tool applies fixed rules, so the same facts always give
the same verdict, and records the result in trader/research.json. trader-entry prints it; trader-broker's auto-approve requires CLEAR.

    python3 research.py record facts.json            # one stock, or {"stocks": [...]}; "-" reads stdin
    python3 research.py show                         # every recorded verdict, today's first
    python3 research.py check RELIANCE               # exit 0 CLEAR, 1 CAUTION, 2 SKIP, 3 no research today
    python3 research.py rules                        # the thresholds in force

The verdict is advisory. CLEAR means nothing in the facts argues against trading it today, not
that it is a good trade. SKIP and CAUTION are for you to weigh before /trader-budget; they never
change a size or a limit, and they never block an order. trader-risk does not read this file.

Facts format (every key but symbol optional; missing facts are listed as "not checked"):

  {"symbol": "RELIANCE", "asof": "2026-10-26",
   "price": {"last": 1190.0, "change_pct": 0.46},
   "scorecard": {"performance": 2.9, "valuation": 2.0, "growth": 3.1, "profitability": 7.0,
                 "entry_point": "Good"},
   "red_flags": {"asm": false, "gsm": false, "pledged": false, "default_risk": false,
                 "unsolicited": false},
   "events": [{"kind": "results", "date": "2026-10-27", "note": "Q2 results", "direction": "unknown"}],
   "analyst": {"count": 24, "buy_pct": 100, "upside_pct": 42.0},
   "ownership": {"promoter_pct": 50.44, "promoter_change": -0.04, "fii_change": -0.5, "dii_change": 0.5},
   "bulk_deals": [{"date": "2026-10-23", "client": "X", "side": "sell", "value_cr": 150}]}

  events[].kind: results | board_meeting | rating | halt | dividend | bonus_split | ipo | other
  events[].direction (rating events): neg | pos | unknown
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "trader-market"))
import market  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RULES = {
    "skip_if_red_flag": ["asm", "gsm", "default_risk"],
    "caution_if_red_flag": ["pledged", "unsolicited"],
    "skip_event_kinds_today": ["results", "board_meeting", "halt"],
    "caution_event_kinds_next_days": ["results", "board_meeting"],
    "caution_days_ahead": 3,
    "caution_recent_negative_rating_days": 3,
    "caution_recent_halt_days": 2,
    "caution_big_sell_deal_cr": 100.0,
    "caution_bulk_days": 5,
    "caution_fii_change_pts": -1.0,
    "caution_move_pct": 4.5,
    "note_weak_score_below": 3.0,
}
VERDICT_RANK = {"CLEAR": 0, "CAUTION": 1, "SKIP": 2}
KINDS = {"results", "board_meeting", "rating", "halt", "dividend", "bonus_split", "ipo", "other"}


class ResearchError(Exception):
    pass


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _date(text):
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def today_india(now=None):
    return market.to_local(now or market.utc_now()).date()


def judge(facts, today):
    """facts dict -> {"verdict", "reasons", "notes", "not_checked"}. Pure function."""
    r = RULES
    reasons, notes, missing = [], [], []
    level = "CLEAR"

    def bump(to, text):
        nonlocal level
        if VERDICT_RANK[to] > VERDICT_RANK[level]:
            level = to
        reasons.append("%s: %s" % (to, text))

    flags = facts.get("red_flags")
    if isinstance(flags, dict):
        labels = {"asm": "the stock is in the ASM (additional surveillance) list",
                  "gsm": "the stock is in the GSM (graded surveillance) list",
                  "default_risk": "the default probability is flagged high",
                  "pledged": "a large part of the promoter holding is pledged",
                  "unsolicited": "unsolicited tips are circulating about it"}
        for k in r["skip_if_red_flag"]:
            if flags.get(k):
                bump("SKIP", labels[k])
        for k in r["caution_if_red_flag"]:
            if flags.get(k):
                bump("CAUTION", labels[k])
    else:
        missing.append("red flags")

    events = facts.get("events")
    if events is None:
        missing.append("announcements and events")
        events = []
    for e in events:
        kind = str(e.get("kind") or "other").lower()
        d = _date(e.get("date"))
        note = str(e.get("note") or kind).strip()
        if d is None:
            continue
        delta = (d - today).days
        if kind in r["skip_event_kinds_today"] and delta == 0:
            bump("SKIP", "%s today (%s): the day gaps or whipsaws on it" % (kind.replace("_", " "), note))
        elif kind in r["caution_event_kinds_next_days"] and 0 < delta <= r["caution_days_ahead"]:
            bump("CAUTION", "%s in %d day%s (%s)" % (kind.replace("_", " "), delta, "" if delta == 1 else "s", note))
        elif kind in r["caution_event_kinds_next_days"] and -1 <= delta < 0:
            bump("CAUTION", "%s %s (%s): the reaction may still be playing out"
                 % (kind.replace("_", " "), "yesterday" if delta == -1 else "just now", note))
        elif kind == "halt" and -r["caution_recent_halt_days"] <= delta < 0:
            bump("CAUTION", "trading halt or suspension notice %d day(s) ago (%s)" % (-delta, note))
        elif kind == "rating" and str(e.get("direction", "")).lower() == "neg" and \
                -r["caution_recent_negative_rating_days"] <= delta <= 0:
            bump("CAUTION", "negative credit-rating action on %s (%s)" % (d.isoformat(), note))
        elif kind in ("dividend", "bonus_split") and -1 <= delta <= 1:
            notes.append("%s around %s (%s): the price adjusts on the ex-date" % (kind.replace("_", " "), d.isoformat(), note))
        elif kind == "ipo" and abs(delta) <= 30:
            notes.append("IPO-related item (%s)" % note)

    move = _f((facts.get("price") or {}).get("change_pct"))
    if move is None:
        missing.append("today's move")
    else:
        c = market.near_circuit(move)
        if c:
            bump("CAUTION", "up/down %.2f%% today, close to the %d%% price band: a circuit lock can stop fills" % (move, c))
        elif abs(move) >= r["caution_move_pct"]:
            bump("CAUTION", "moved %.2f%% today already" % move)

    deals = facts.get("bulk_deals")
    if deals is None:
        missing.append("bulk and block deals")
    else:
        for dl in deals:
            d = _date(dl.get("date"))
            v = _f(dl.get("value_cr")) or 0.0
            if d and 0 <= (today - d).days <= r["caution_bulk_days"] and \
                    str(dl.get("side", "")).lower() == "sell" and v >= r["caution_big_sell_deal_cr"]:
                bump("CAUTION", "bulk/block SELL of Rs %.0f crore on %s by %s" % (v, d.isoformat(), dl.get("client", "?")))

    own = facts.get("ownership")
    if own is None:
        missing.append("ownership")
    else:
        fii = _f(own.get("fii_change"))
        if fii is not None and fii <= r["caution_fii_change_pts"]:
            bump("CAUTION", "FII holding fell %.2f points last quarter" % abs(fii))
        pc = _f(own.get("promoter_change"))
        if pc is not None and pc <= -1.0:
            bump("CAUTION", "promoter holding fell %.2f points last quarter" % abs(pc))

    sc = facts.get("scorecard")
    if sc is None:
        missing.append("scorecard")
    else:
        weak = [k for k in ("performance", "valuation", "growth", "profitability")
                if (_f(sc.get(k)) is not None and _f(sc.get(k)) < r["note_weak_score_below"])]
        strong = [k for k in ("profitability",) if (_f(sc.get(k)) or 0) >= 7]
        if weak:
            notes.append("weak Tickertape scores: %s (months-long view, not an intraday signal)" % ", ".join(weak))
        if strong:
            notes.append("strong: " + ", ".join(strong))
        if str(sc.get("entry_point", "")).lower() == "poor":
            notes.append("Tickertape's entry-point flag says Poor")

    an = facts.get("analyst")
    if an:
        notes.append("analysts: %s ratings, %s%% buy, upside %s%% (a consensus, not a signal for today)"
                     % (an.get("count", "?"), an.get("buy_pct", "?"), an.get("upside_pct", "?")))
    return {"verdict": level, "reasons": reasons, "notes": notes, "not_checked": missing}


# ---------------------------------------------------------------- files

def load_doc(path):
    if not os.path.exists(path):
        return {"stocks": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as e:
        raise ResearchError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(doc.get("stocks"), dict):
        doc["stocks"] = {}
    return doc


def save_doc(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def clean(facts):
    if not isinstance(facts, dict) or not facts.get("symbol"):
        raise ResearchError("each stock needs a \"symbol\"")
    try:
        sym = market.clean_symbol(facts["symbol"])
    except market.MarketError as e:
        raise ResearchError(str(e))
    for e in facts.get("events") or []:
        if str(e.get("kind", "other")).lower() not in KINDS:
            raise ResearchError("event kind %r is not one of %s" % (e.get("kind"), ", ".join(sorted(KINDS))))
    return sym


def cmd_record(a):
    raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ResearchError("facts are not valid JSON: %s" % e)
    items = data["stocks"] if isinstance(data, dict) and "stocks" in data else [data]
    now = market.parse_now(a.now, error=ResearchError)
    today = today_india(now)
    doc = load_doc(a.out)
    out = []
    for facts in items:
        sym = clean(facts)
        asof = _date(facts.get("asof")) or today
        res = judge(facts, today)
        rec = {"symbol": sym, "date": today.isoformat(), "asof": asof.isoformat(),
               "recorded_at": market.to_local(now).strftime("%Y-%m-%d %H:%M"),
               "verdict": res["verdict"], "reasons": res["reasons"], "notes": res["notes"],
               "not_checked": res["not_checked"], "source": "tickertape", "facts": facts}
        doc["stocks"][sym] = rec
        out.append(rec)
    doc["updated"] = market.to_local(now).strftime("%Y-%m-%d %H:%M")
    save_doc(a.out, doc)
    for rec in out:
        print(render(rec))
    return 0


def render(rec):
    lines = ["%-9s %s (as of %s)" % (rec["verdict"], rec["symbol"], rec.get("asof", rec["date"]))]
    for x in rec["reasons"]:
        lines.append("    - " + x)
    for x in rec["notes"]:
        lines.append("    ~ " + x)
    if rec["not_checked"]:
        lines.append("    not checked: " + ", ".join(rec["not_checked"]))
    return "\n".join(lines)


def cmd_show(a):
    doc = load_doc(a.out)
    recs = sorted(doc["stocks"].values(), key=lambda r: (r.get("date") != a.today_str(), -VERDICT_RANK[r["verdict"]], r["symbol"]))
    if not recs:
        print("No research recorded yet (%s)." % a.out)
        return 1
    for r in recs:
        print(render(r))
    print("\nAdvisory only. Education and tooling, not financial advice.")
    return 0


def cmd_check(a):
    doc = load_doc(a.out)
    now = market.parse_now(a.now, error=ResearchError)
    today = today_india(now).isoformat()
    try:
        sym = market.clean_symbol(a.symbol)
    except market.MarketError as e:
        raise ResearchError(str(e))
    rec = doc["stocks"].get(sym)
    if not rec:
        print("No research for %s. Run the research steps first." % sym)
        return 3
    if rec.get("date") != today:
        print("Research for %s is from %s, not today. Run it again." % (sym, rec.get("date")))
        return 3
    print(render(rec))
    return VERDICT_RANK[rec["verdict"]]


def cmd_rules(a):
    print(json.dumps(RULES, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Research verdicts from Tickertape facts (trader-research).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--out", default="trader/research.json")
        p.add_argument("--now", help="India time like \"2026-10-26 08:30\"")

    p = sub.add_parser("record", help="judge facts and record the verdict")
    p.add_argument("file", help="facts JSON file, or - for stdin")
    common(p)
    p = sub.add_parser("show", help="print recorded verdicts")
    common(p)
    p = sub.add_parser("check", help="status of one symbol (exit 0 clear, 1 caution, 2 skip, 3 none today)")
    p.add_argument("symbol")
    common(p)
    p = sub.add_parser("rules", help="print the thresholds")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        if a.cmd == "show":
            now = market.parse_now(a.now, error=ResearchError)
            a.today_str = lambda: today_india(now).isoformat()
        return {"record": cmd_record, "show": cmd_show, "check": cmd_check, "rules": cmd_rules}[a.cmd](a)
    except ResearchError as e:
        print("research.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
