# Capital.com connector

Connects Qivynex Trade to a Capital.com account through the official Capital.com REST API.
It runs as a Docker container on the Linux VPS. There's no approval wait: you can create the API key yourself.

## 1. Create the API key (in Capital.com)

1. Log in at **https://capital.com** (web platform) and switch to your **Demo** account.
2. Turn on **two-factor authentication**: Settings → Security. Capital.com requires it before creating API keys.
3. Go to **Settings → API integrations → Generate API key**.
   - Label: `Qivynex Trade`
   - **Custom password**: a NEW password just for the API (not your login password). Avoid the `'` character.
   - Expiry: optional (default 1 year)
4. Enter your 2FA code. **Copy the API key immediately**; it is shown only once.

## 2. Connect it on the VPS

```bash
sudo /opt/trading_app/deploy/update.sh          # make sure the latest code is installed
sudo /opt/trading_app/deploy/setup-capital.sh   # enter: login email, API key, API key password, "demo"
```

The values go into `/opt/trading_app/.env` (readable by root only). A working connection logs:

```
Logged in to Capital.com demo, account ...
EURUSD: epic EURUSD, 1 size = 1.0 units, min ..., step ..., digits 5
```

## 3. Use it in the dashboard

1. **System → Use broker data.** Quotes and about 1,000–5,000 candles per symbol/timeframe appear within a minute.
2. Backtest on the real history, then switch the mode to **PAPER**, then **BROKER DEMO**.
3. Trades placed by the app appear in your Capital.com demo account with a stop-loss and take-profit.

## Safety built into the connector

- Re-checks every signed order: signature, expiry, demo order ↔ demo environment, volume ≤ `CONNECTOR_MAX_VOLUME`
  (0.10 lots), stop-loss present and on the correct side, spread ≤ `CONNECTOR_MAX_SPREAD_POINTS`, no duplicates.
- Orders are sent **with the stop-loss attached**. After the fill it reads the position back from Capital.com and checks:
  - the **stop-loss** is there (otherwise the position is closed immediately and the app locks trading);
  - the **real position size** matches what was intended (otherwise it is closed immediately). This guards against
    any misunderstanding of Capital.com's size units.
- If the size units for a market are ambiguous, the connector refuses to trade that market and tells you to set
  `CAPITAL_UNITS_PER_SIZE` (1 = size in units, 100000 = size in lots).
- Capital.com has no order labels, so the connector remembers which positions it opened (in the `capital_state`
  volume). Positions you open manually in Capital.com are ignored.
- Live trading needs `CAPITAL_ENV=live`, `ALLOW_REAL_ACCOUNT=true` **and** the app's approved live gate.
- Only one broker connector should run at a time; the setup scripts switch the other one off.

## Notes

- Capital.com charges no commission on forex CFDs (the cost is in the spread). Set **Risk → Commission per lot** to `0`
  in the dashboard so position sizing isn't too conservative.
- Realised P&L for closed trades is calculated from the open and close prices. Overnight funding isn't included; check
  your Capital.com statement for exact figures.

```bash
cd /opt/trading_app
docker compose logs -f capital      # live log
docker compose restart capital
```
