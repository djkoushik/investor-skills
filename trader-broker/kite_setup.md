# Connecting Zerodha Kite

trader-broker talks to Zerodha through the **Kite connector** (Zerodha's MCP server),
not through keys in this repo. Nothing in this pack stores a token or a password.

1. In Claude, add the Kite connector (Customize, Connectors) and log in with the
   `login` tool. The login is a Zerodha page you open yourself; Kite sessions end
   every day, so log in again each morning before the open.
2. Set the tool permissions to **Needs approval** for `place_order`, `modify_order`,
   `cancel_order`, `place_gtt_order`, `modify_gtt_order` and `delete_gtt_order`.
   Never "Always allow". Claude's own Allow once / Deny card is a second check
   after your typed yes.
3. Read-only tools (`get_orders`, `get_positions`, `get_margins`, `get_quotes`,
   `get_ltp`, `get_historical_data`) can stay on automatic.
4. `"paper": true` in `trader/config.json` is practice mode: tickets are prepared
   and your yes is recorded, but Claude does not call `place_order`. Zerodha has no
   paper account, so set `"paper": false` only when you mean real money.

What Kite can and cannot do for this pack:

- **No bracket orders.** An entry is a LIMIT order on product MIS. When it fills,
  `ticket.py exits` gives the stop-loss (SL: trigger plus a limit a few ticks lower)
  and the target (LIMIT). When one fills you cancel the other. Keep an eye on both.
- **MIS auto square-off.** Zerodha closes open MIS positions itself around 15:25
  (earlier, 15:12, for stocks in the call-and-auction segment) and charges about
  Rs 50 plus GST per order. `flat_by` is 15:10 so you close first.
- **MARKET orders** are restricted for illiquid stocks; this pack uses limit orders.
- Check the live charges and square-off rules at zerodha.com before you rely on them.
