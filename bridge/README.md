# MT5 bridge (Windows)

The MetaTrader 5 terminal only runs on Windows, so this small Python service runs next to it and talks to
the dashboard server over HTTPS. Every request is HMAC-signed with the shared `BRIDGE_SECRET`.

## Setup

1. **Windows VPS or PC** that stays on (a small Windows VPS is ideal). Sync the clock (Settings → Time → *Sync now*);
   requests more than 30 s off are rejected.
2. Install **MetaTrader 5** from your broker, log in to the **demo** account, and enable
   *Tools → Options → Expert Advisors → Allow algorithmic trading*. Press the **Algo Trading** button so it is green.
3. Install **Python 3.11+ (64-bit)** from python.org (tick *Add to PATH*).
4. Copy the `bridge` folder to the machine, then in that folder:
   ```bat
   pip install -r requirements.txt
   copy bridge.env.example bridge.env
   ```
5. Edit `bridge.env`:
   - `BRIDGE_SECRET` = the value from the server: `grep BRIDGE_SECRET /opt/trading_app/.env`
   - `SYMBOL_MAP` if your broker uses suffixes (e.g. `EURUSD=EURUSD.m,GBPUSD=GBPUSD.m,USDJPY=USDJPY.m`)
   - leave `ALLOW_REAL_ACCOUNT=false` until the live gate is passed
6. Run `run_bridge.bat`. You should see `Connected to MT5 ...` and the dashboard's **MT5 bridge** tile turns green.
7. Keep it running: Task Scheduler → *Create Task* → trigger *At log on*, action `run_bridge.bat`,
   *Run whether user is logged on or not* is **not** possible for MT5 (needs a desktop session), so use
   auto-logon on the VPS.
8. Optional hardening: put the Windows VPS public IP in `BRIDGE_ALLOWED_IPS` in the server `.env`, then
   `sudo /opt/trading_app/deploy/update.sh`.

## What it does every few seconds

- heartbeat with account balance/equity/margin and account type (demo/real)
- symbol specifications (contract size, tick value, lot limits) hourly
- quotes, and **closed** M15/H1 candles (converted from broker server time to UTC)
- fetches signed orders, re-checks them locally, executes, verifies the stop-loss at the broker
- reports open positions and closed deals for reconciliation

## Bridge-side safety (independent of the server)

Refuses orders that: have an invalid signature · are expired · are for the wrong account type
(demo order on a real account or vice-versa) · exceed `MAX_VOLUME` · lack a stop-loss · would duplicate
an existing position · face a spread above `MAX_SPREAD_POINTS`. If the stop-loss cannot be confirmed at
the broker, the position is closed immediately and the server locks trading.

Logs: `bridge.log` (rotated).
