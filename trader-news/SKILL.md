---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch
  and flags what moves it: Yahoo Finance headlines per ticker (India edition),
  each company's corporate announcements on the NSE (financial results, board
  meeting outcomes, dividends, bonus and splits, acquisitions, director
  changes, credit ratings, trading halts), and how far each stock is from
  yesterday's close, with a flag when it is brushing its circuit limit. Shows
  only what is new since the last check, newest first, in India time, tags
  each item (earnings, analyst, deal, halt, regulatory, macro...) and never
  repeats one. Use when the user says "any news on RELIANCE", "why is this
  moving", "what's the news on my watchlist", "morning briefing", "pre-market
  news", "monitor the news", "did anything break", "check the announcements",
  or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

## Job and boundaries
- **Does:** reports what is new since the last check for each watched stock: NSE announcements, Yahoo headlines, today's move and circuit proximity, and cross-checks the two sources.
- **Does not:** predict, place orders, treat a headline as fact before reading the source, or give analyst views (those are Tickertape's).
- **Needs first:** a ticker list, `trader/watchlist.json`, or `trader/plan.json`.
- **Hands off to:** `/trader-entry` for a clean stock; results, halt or regulatory items are said first with advice to skip the stock today.
- **Stop and ask the user when:** the NSE feed is blocked (items become 'PRESS ONLY' or 'not checked'), or a story is paywalled and could not be read.


A chart can look perfect and still be about to gap on a results announcement,
a board-meeting outcome, a trading halt or a SEBI order. Before a setup becomes
a trade, this skill reads the two feeds that carry those first, for every
stock on the watchlist, and tells you only what is new. It also prints how far
each stock has moved today, so a headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE INFY --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-filings --json     # headlines only
```

Other flags: `--all` (show seen items too), `--no-moves` (skip the price
line), `--move-pct 3` (flag a move this big), `--now "2026-10-26T10:15"`
(India time), `--offline DIR`, `--dry-run` (do not update the state file).
`--no-sec` is kept as an alias of `--no-filings`.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| NSE corporate announcements | `nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE`, read through a cookie session the way the NSE website does. Unofficial: NSE can rate-limit or block it, and then filings are skipped with a note |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the announcement and read it. Quote the source
  and link it. Never summarise a paywalled story you could not read: say so.
- **Announcements beat headlines.** If a filing and a headline disagree, the
  filing is what the company told the exchange.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. Announcements marked `!` are the important kinds:
  financial results, board meeting outcomes, acquisitions and schemes,
  director or management changes, credit ratings, defaults and insolvency,
  delisting, open offers, dividends, bonus, buy-back, splits, preferential
  issues and rights issues, trading halts and suspensions.
- **Results dates matter most.** A stock reporting results today or tonight
  gaps either way; an intraday trade is not exposed overnight, but the day
  itself is wild. Say so out loud.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too. A move near 5, 10 or
  20 % is flagged as possibly at its circuit: such a stock may not fill a buy
  or a stop.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.
- **Fundamentals and analyst views are not here.** For ratings, target prices,
  scorecards and company financials the user has Tickertape: point to it
  rather than guess.

## Steps

1. Run it with the watchlist, or the symbols the user names. Add `--hours 12`
   for a morning briefing.
2. Read the new items newest first. Open the important ones.
3. Report: what is new, per symbol, one line each, with the tag, the time
   (IST) and the link, then the moves. Say plainly when there is nothing new.
4. If a results announcement, a halt or a regulatory item is among them, say
   that before anything else and suggest skipping that stock today.
5. Hand on to `/trader-entry` for a stock that is clean.

## Example (the repo's offline test fixture: synthetic items, not real news)

```
RELIANCE     ₹1,492.40 +1.18% vs yesterday's close ₹1,474.95

  2026-10-23 10:30 IST  RELIANCE    NSE filing    Reliance Industries Limited: Analysts/Institutional Investor Meet/Con. Call Updates
      tags: earnings  https://nsearchives.nseindia.com/corporate/RIL_call.pdf
  2026-10-23 09:00 IST  RELIANCE    Yahoo Finance Brokerage raises target price on Reliance to Rs 1,650, keeps buy
      tags: earnings, analyst  https://example.test/news/ril-target
! 2026-10-23 08:05 IST  RELIANCE    NSE filing    Reliance Industries Limited: Financial Results
      tags: earnings  https://nsearchives.nseindia.com/corporate/RIL_results.pdf
```

## What it never does

It never places an order, never says what a stock will do, and never calls
a headline a fact before the source is read.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Intraday
  trading loses money for most people, and SEBI studies of Indian retail traders say the same.
- Practice mode by default (`"paper": true`): Zerodha has no paper account, so in this mode tickets are
  prepared and the yes is recorded but Claude does not call `place_order`. Going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat (or, only on a day the user switched automation on in their own words, the order passes `ticket.py auto-approve`), and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- Cash-segment stocks and ETFs only: no futures, no options, no margin and no short selling unless the
  config allows it and the user confirms.
- No passwords, tokens or API secrets in the chat. The Kite login is done by the user on Zerodha's page.

## Related
Run `/trader-research` first if the Tickertape connector is available: it flags ASM/GSM, pledged shares, results dates and big sell deals that headlines can miss.

## Cross-check: what both sources agree on

Yahoo headlines and NSE filings are compared, per stock, within 48 hours. A headline about a hard event (results, deal, dividend or buyback, people change, risk, legal, trading halt, regulatory) gets one of: **CONFIRMED by NSE filing** (the two sources agree: act on the filing's facts), **PRESS ONLY** (no matching filing yet: treat as a rumour until it shows up), or **NSE feed not checked** (the exchange feed failed, so nothing can be confirmed). A filing nobody wrote about is **EXCHANGE ONLY**: official, but not yet in the press. If only one source answers for a stock, the report says so. A summary line counts each kind. Commentary headlines (analyst views, market talk) get no verdict.
