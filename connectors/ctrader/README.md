# cTrader connector

Connects Qivynex Trade to a cTrader account through the official **cTrader Open API**
(JSON over WebSocket). It runs as a Docker container on the same Linux VPS as the app, so no
Windows machine is needed.

## 1. Get API credentials (one time)

1. Go to **https://openapi.ctrader.com** and log in with the same **cTrader ID** you use for your demo account.
2. Open **Applications → Add new app**. Name it e.g. "Qivynex Trade", describe it
   ("Personal automated trading app for my own cTrader accounts"), and save.
3. The app shows as **Submitted**. Spotware reviews it and emails you; this can take a few days.
   You can't continue until it shows **Active**.
4. When it's active, open the app and note the **Client ID** and **Client Secret** (Credentials).
5. Click **Playground** → **Get token** → choose the **trading** scope → log in and tick your **demo** account →
   allow. The Playground shows an **access token** and a **refresh token**. Copy both.
6. Note your **account number** as shown in cTrader (e.g. 5123456).

Keep these private. Anyone with them can trade on your account.

## 2. Connect it in the dashboard

**Brokers** page → cTrader card: paste Client ID, Client secret, access token and refresh token, click **Save
credentials**, then **Use this broker**.

Alternative (command line):

```bash
sudo /opt/trading_app/deploy/update.sh          # get the latest code
sudo /opt/trading_app/deploy/setup-ctrader.sh   # paste the values from step 1
```

The script stores the values in `/opt/trading_app/.env` (readable by root only), enables the
`ctrader` service and shows its log. A working connection logs:

```
Using cTrader account 5123456 (demo) at <broker>
```

## 3. Switch the app to your broker

In the dashboard (`https://trade.qivynex.com`):

1. **System → Market data source → Use broker data.** Within a minute you'll see live quotes and
   about 5,000 candles of history per symbol/timeframe.
2. Run **Backtests** on that history.
3. **Dashboard → mode PAPER** (simulated fills at real prices) or **BROKER DEMO** (real orders on the cTrader demo account).
4. Watch the trades in cTrader as well: positions are tagged with label `qt` and the order ID as comment.

## How it works / safety

- Same signed protocol and server-side risk engine as the MT5 bridge; the connector re-checks every order:
  valid signature, not expired, demo order ↔ demo account, volume ≤ `CONNECTOR_MAX_VOLUME` (0.10 lots),
  stop-loss present, spread ≤ `CONNECTOR_MAX_SPREAD_POINTS`, no duplicate.
- Market orders are sent **with a protective stop attached** (cTrader requires a relative stop for market orders),
  then amended to the exact stop-loss/take-profit and **verified at the broker**. If no stop-loss can be
  confirmed, the position is closed immediately and the app locks trading.
- Only positions labelled `qt` are managed; trades you place by hand in cTrader are ignored.
- Live accounts are refused unless `ALLOW_REAL_ACCOUNT=true` is set in `.env` **and** the app's live gate is approved.
- Access tokens last about 30 days. The connector refreshes them automatically and keeps the new tokens in the
  `ctrader_state` Docker volume. If you ever revoke access, run `setup-ctrader.sh` again with new Playground tokens.

## Useful commands

```bash
cd /opt/trading_app
docker compose logs -f ctrader          # live log
docker compose restart ctrader          # reconnect
docker compose stop ctrader             # disconnect (the app then blocks new broker orders: heartbeat stale)
```

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CTRADER_ACCOUNT_LOGIN` | (only demo account) | Account number to trade |
| `CTRADER_SYMBOL_MAP` | | Broker symbol names, e.g. `EURUSD=EURUSD.r,GBPUSD=GBPUSD.r` |
| `CONNECTOR_MAX_VOLUME` | `0.10` | Hard cap per order in lots |
| `CONNECTOR_MAX_SPREAD_POINTS` | `30` | Refuse orders above this spread |
| `ALLOW_REAL_ACCOUNT` | `false` | Must be `true` before any real-money order |
