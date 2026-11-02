# Qivynex Trade — AI-assisted Forex trading system

Private, self-hosted Forex system built from the *AI Forex System One-Time Implementation Plan*:
MT5 market data → indicators → one documented strategy → **deterministic risk engine** →
execution (paper / MT5 demo / MT5 live behind a gate) → dashboard, audit trail and alerts.

> **Risk notice.** This software does not make trading profitable. The included strategy is a
> testable hypothesis, not a proven edge. Automated trading can lose some or all deposited
> funds. Run backtests, then paper, then MT5 **demo** for weeks before considering live trading.

## What's inside

| Part | Tech | Purpose |
|---|---|---|
| `backend/` | Python 3.12, FastAPI, SQLAlchemy, PostgreSQL, pandas | API, auth, strategy, risk engine, paper broker, backtester, bridge API |
| `backend` worker | same image, `python -m app.worker` | Trading engine loop (single instance, DB advisory lock) |
| `frontend/` | React 19 (esbuild), nginx | Dashboard served at `https://trade.qivynex.com` |
| `connectors/capital/` | Python, Capital.com REST API | **Capital.com connector** — runs on the Linux VPS, instant API key |
| `connectors/ctrader/` | Python, cTrader Open API (WebSocket) | **cTrader connector** — runs on the Linux VPS, no Windows needed |
| `bridge/` | Python + `MetaTrader5` package | Alternative: runs on Windows next to an MT5 terminal |
| `deploy/` | bash, Docker Compose | One-command VPS install, updates, backups |

```
MT5 terminal (Windows) ──HTTPS + HMAC──►  nginx ─► FastAPI ─► PostgreSQL
   ▲  mt5_bridge.py                         │         ▲
   └── signed orders ◄──────────────────────┘     worker (engine)
```

## Dashboard pages

| Page | What it's for |
|---|---|
| Dashboard | Mode switch (Off / Signals / Paper / Broker demo / Broker live), kill switch, loss limits, quotes |
| Live trading | Real-time equity and P&L, open positions, **New order** ticket (risk-checked), activity feed, positions opened outside the app |
| Market / Signals / Trades | Charts with indicators, every signal with its reasons and risk checks, orders and performance |
| Backtest | Strategy tests on broker history, with costs and robustness warnings |
| Risk & Strategy | Risk limits, strategy parameters, live-trading gate |
| Brokers | Enter credentials, choose the active broker, demo or live, connector limits, connection status |
| AI Assistant | Your own OpenAI / Claude key; a coach that explains your data and teaches. It can't trade |
| Guide | Getting-started checklist, how the app decides, daily routine, glossary |

## Security

- **No sign-up.** Only users with `users.manage` can create accounts. First admin is created by the installer.
- 13 granular permissions (view, backtest, strategy edit, risk edit, trading control, kill switch, live approval, user admin, audit…) plus role presets.
- scrypt password hashing, 12+ char policy, forced change of temporary passwords.
- Optional / enforceable **TOTP 2FA** per user.
- Server-side sessions in HttpOnly + Secure + SameSite=Strict cookies, idle timeout (30 min) and absolute timeout (12 h), revocable.
- CSRF token on every state-changing request.
- Account lockout (5 failures → 15 min), per-IP throttling, nginx rate limits on login.
- Password re-entry for risk increases, MT5 modes and the live gate; typed confirmation phrase for live trading.
- **Append-only audit log** enforced by a PostgreSQL trigger (UPDATE/DELETE rejected).
- Bridge API: HMAC-SHA256 signed requests, 30 s clock-skew window, single-use nonces, optional IP allowlist. Orders carry their own HMAC signature which the bridge verifies.
- Strict CSP and security headers, containers run read-only, non-root, DB never exposed.
- Broker credentials and AI keys are encrypted in the database (Fernet, key derived from `SECRET_KEY`), never returned
  to the browser, and served only to the connector containers on the private Docker network.
- Only the broker selected on the Brokers page can feed data or receive orders (enforced by the server).

## Trading safety controls (from the plan)

- Risk per trade 0.5 % (sized from `min(equity, reference balance)` — risk never grows automatically), daily 2 %, weekly 4 %, 1 concurrent position.
- Stop-loss mandatory and verified at the broker; martingale, grid, averaging down and hedging blocked.
- Min-lot check: if 0.01 lot already exceeds the risk budget the trade is rejected.
- Spread, slippage, data freshness, bridge heartbeat, margin and duplicate-order checks.
- Loss-limit breach **locks** new trades until a manual review.
- Kill switch (UI, or CLI: `docker compose exec backend python -m app.cli kill-switch on`).
- Reconciliation with broker positions; any unexplained difference blocks new trades.
- Live gate: 5-item checklist + approval with password; the bridge additionally refuses real accounts unless `ALLOW_REAL_ACCOUNT=true`.

**Risk profiles.** Limits are kept separately for *practice* (PAPER, crypto paper), *broker demo* (MT5_DEMO, crypto
testnet) and *real money* (MT5_LIVE, crypto live) — Risk & Strategy → Risk policy tabs. Real-money limits can never be
looser than demo (rejected on save and clamped on read). Autopilot and practice changes never touch the real-money profile.

**Correlated pairs.** `max_same_currency_positions` (default 2) limits open positions leaning the same way on one
currency, e.g. long EURUSD + long GBPUSD + long AUDUSD would be three USD shorts.

**Real-money auto-trade** needs: an approved, unexpired live gate; the RESEARCH_SUPPORTED verdict; and the per-mode
switch turned on with your password. When the gate expires or is revoked, the engine leaves BROKER LIVE / CRYPTO LIVE
and switches real-money auto-trade off — re-enabling needs the password again. Live-gate trade evidence counts only
broker-demo / testnet trades (practice fills are too optimistic).

Trading modes: `OFF` → `SIGNALS_ONLY` → `PAPER` → `MT5_DEMO` (shown as *Broker demo*) → `MT5_LIVE` (*Broker live*).
The broker modes work with any connector (Capital.com, cTrader or MT5); run one at a time.

## Deploy to the VPS (trade.qivynex.com)

1. **DNS:** create an `A` record `trade.qivynex.com → 37.60.252.21`.
2. **Install** (Ubuntu 22.04/24.04, as root):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/mianwaseemabas/trading_app/main/deploy/install.sh -o install.sh
   sudo ADMIN_USER=abbas ACME_EMAIL=you@example.com bash install.sh
   ```
   The installer installs Docker if needed, clones to `/opt/trading_app`, generates `.env` with random
   secrets, builds and starts the stack, detects your web server and wires up HTTPS:
   - existing **nginx** → adds a site + Let's Encrypt via certbot
   - existing **Apache** → adds a vhost + certbot
   - existing **Traefik** container → adds labels/network (`docker-compose.override.yml`)
   - nothing on ports 80/443 → starts the bundled **Caddy** with automatic HTTPS
   It prints the admin username and a temporary password (also in `/root/qivynex-trade-credentials.txt`).
3. Open `https://trade.qivynex.com`, sign in, set your own password, enable 2FA (**My account**).
4. Create users under **Users** and pick their permissions.

### Operations

```bash
sudo /opt/trading_app/deploy/update.sh        # pull latest code from GitHub + rebuild (backs up DB first)
sudo /opt/trading_app/deploy/backup.sh        # manual DB backup (daily cron is installed automatically)
sudo /opt/trading_app/deploy/restore.sh backups/trading-YYYYmmdd-HHMM.sql.gz
cd /opt/trading_app && docker compose logs -f --tail=100 backend worker
docker compose exec backend python -m app.cli reset-password abbas
docker compose exec backend python -m app.cli disable-2fa abbas
```

**Auto-deploy from GitHub:** run `sudo /opt/trading_app/deploy/setup-autodeploy.sh` once on the VPS and add
the 4 secrets it prints (`VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY`) under GitHub → Settings → Secrets
and variables → Actions. From then on every push to `main` runs the tests and, if they pass, updates the VPS
(database backup first). The deploy key can only run `deploy/update.sh` — nothing else.

## Getting data and testing

- **Try it without a broker:** System → Data source → *Simulator* (synthetic prices, clearly labelled). Set mode
  to `PAPER` to watch signals, risk checks and paper fills. Never judge the strategy on simulated data.
- **Connect a broker from the dashboard:** open **Brokers**, enter your Capital.com (or cTrader) demo
  credentials, click *Use this broker*. No server commands needed; credentials are stored encrypted.
- **Real data with Capital.com:** see `connectors/capital/README.md` for creating the API key.
- **Real data with cTrader:** follow `connectors/ctrader/README.md`, then run
  `sudo /opt/trading_app/deploy/setup-ctrader.sh` and choose System → *Use broker data*.
- **Real data with MT5:** set up the Windows bridge (`bridge/README.md`) and choose *Use broker data*.
  Either connector backfills ~5,000 bars per symbol/timeframe.
- **Backtest:** Backtest page. Uses the same strategy and risk code as live, next-bar entries, spread,
  slippage, commission and swap, in-sample/out-of-sample split and segment stability, with honest
  robustness warnings. You can also import MT5 "Export Bars" CSV files for longer history.

## Markets and strategies

Default forex watch-list: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP (existing installs
get the missing ones added once; edit on Risk & Strategy, max 20). Crypto: BTC, ETH, SOL, XRP, BNB (USDT). Gold/indices
work too, but broker names differ (Capital.com uses `GOLD`, not `XAUUSD` — set `SYMBOL_MAP` in the connector if needed).

Strategies (all closed-candle, no look-ahead, tested in CI): EMA trend pullback (baseline), SMC sweep → BOS → retest,
Donchian breakout, RSI-2 mean reversion, Bollinger squeeze, EMA 9/21 + ADX, **London breakout** (Asian range, 07–10 UTC),
**Supertrend + EMA 200**, **Ichimoku TK cross**. None is a proven edge: compare them in the Strategy lab on your
broker's data, with costs and the untouched final-test period, before trusting any of them.

## Dependencies and database migrations

- Exact versions are pinned in `backend/requirements.lock`, `connectors/*/requirements.lock` and
  `frontend/package-lock.json`; Docker images and CI install only those. To upgrade: change `requirements.txt` /
  `package.json`, regenerate the lock (`pip freeze` in a python:3.12 venv / `npm install --package-lock-only`), commit.
- Schema changes to existing tables are Alembic migrations in `backend/app/migrations/versions/`, applied automatically
  at startup under a database lock (after the pre-deploy backup). Prices, volumes and money are `NUMERIC`.
- CI also runs daily (tests only, never deploys) to catch weekday/time-dependent failures.

## Local development

```bash
# backend (needs PostgreSQL)
cd backend && pip install -r requirements-dev.txt
APP_ENV=dev COOKIE_SECURE=false DATABASE_URL=postgresql+psycopg://trader:trader@localhost/trading \
  INITIAL_ADMIN_USERNAME=dev INITIAL_ADMIN_PASSWORD='Choose#A-Strong1' uvicorn app.main:app --reload
python -m app.worker                                     # engine
APP_ENV=test DATABASE_URL=...trading_test pytest -q     # tests (wipes that DB)
# frontend
cd frontend && npm install && npm run build   # set SERVE_STATIC_DIR=../frontend/dist for the dev API to serve it
```

## Plan acceptance criteria → where they live

| # | Criterion | Implementation |
|---|---|---|
| 1 | Retrieve & validate demo data | `bridge/mt5_bridge.py`, `services/market_data.py` (OHLC sanity, alignment, gaps, stale checks) |
| 2 | Repeatable, versioned signals | `services/strategy.py`; version auto-bumps on parameter change |
| 3 | Every signal + inputs recorded | `signals` table (features, conditions, risk checks) |
| 4 | Risk engine rejects invalid/oversized trades | `services/risk_engine.py` + tests |
| 5 | Place, verify, reconcile demo orders | bridge `execute()`, `/api/bridge/orders/*`, `/api/bridge/positions` |
| 6 | Protective stops verified at broker | bridge SL verification, auto-close if SL can't be set, server lock |
| 7 | Disconnects/errors fail safely | stale data/heartbeat checks, order expiry, reconciliation lock |
| 8 | Dashboard reconciles with broker | MT5 account snapshots, reconciliation status |
| 9 | Kill switch blocks new orders | UI + CLI, checked by server and at dispatch |
| 10 | Backtest & demo results documented | Backtest page, Trades → Performance (net of costs) |
