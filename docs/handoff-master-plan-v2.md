# Handoff — Real-World Trading Improvement Master Plan v2.0

Source plan: `docs/real-world-improvement-plan.md`. Date: 2026-10-31.
Status words: **Implemented+tested** = automated tests pass. **Simulated** = tested only against simulated data or fake providers. **Not verified** = needs checking on the real VPS, broker or exchange.

## 1. Architecture diff
- New services: `quality.py` (quote diagnostics, session state, data health, incidents, segregated accounts), `assess.py` (score, hard gates, economics, verdict, lifecycle, shadow outcomes), `alerts.py` (inbox, dedup, Telegram/email delivery, daily/weekly items), `livegate.py` (evidence, self-test, expiry).
- New router: `routers/trust.py` — `/api/data/health`, `/api/accounts`, `/api/alerts*`, `/api/signal-board`, `/api/signals/{id}/assessment`, `/api/signals/{id}/economics`, `/api/strategies/compare`, `/api/risk/status`, `/api/performance/insights`. Also added: `POST /api/system/risk/pilot-preset`, `POST /api/system/live-gate/self-test` and `POST /api/ai/test`. Routes follow the existing `/api/...` style; no `/v1` prefix was added.
- Worker: `engine.research_jobs` runs incident tracking (30 s), the SMC event log for all markets (120 s), shadow outcomes (60 s), paper reconciliation (5 min), scheduled alerts and alert delivery (5 s).
- Engine/crypto signal flow: DETECTED → SCORED → BLOCKED_DATA / REJECTED / WATCH / CANDIDATE → RISK_ELIGIBLE → AWAITING_APPROVAL → APPROVED/DECLINED/EXPIRED → ORDERED. Signal status and order status are tracked separately. Strategy trades in BROKER LIVE always need approval.
- `audit()` now turns key events into alerts: fills, rejections, stop/target exits, loss lock, kill switch, mode change, trailing stops. The legacy direct Telegram `notify()` only logs now, so messages are not sent twice.

## 2. Database changes (additive only — no existing column changed)
New tables: `data_incidents`, `alerts`, `alert_deliveries`, `signal_assessments`, `signal_events`, `signal_outcomes`. They are created on startup by `Base.metadata.create_all`. Existing data is untouched.
- **Backup:** `deploy/update.sh` runs `deploy/backup.sh` before every deploy. Restore with `deploy/restore.sh`.
- **Rollback:** redeploy the previous commit. The new tables are simply ignored. To remove them: `DROP TABLE data_incidents, alerts, alert_deliveries, signal_assessments, signal_events, signal_outcomes;`
- New settings keys: `alert_prefs`, `assessment`, `risk.max_trades_per_day`, `risk.max_spread_pct_other`, `risk.slippage_pct_other`, `paper_account.start_balance`/`reset_at`. Defaults merge automatically.
- Numbers are still stored as float. Moving to NUMERIC/Decimal needs a real migration tool (Alembic), and that is the recommended next step.

## 3. What was built, by plan section
| Plan item | Status |
|---|---|
| 4.1/4.2 Quote diagnostics (pips, bps, age, session, spread vs ATR, instrument specs) | Implemented+tested |
| 4.2 Weekend/rollover detection explains wide spreads (screenshot finding 1) | Implemented+tested. Root cause on the VPS is **not verified**; check `/api/data/health` during market hours |
| 4.3 BLOCKED_DATA, one incident + one alert per occurrence, recovery logged | Implemented+tested |
| 4.4 Segregated accounts (APP_PAPER, BROKER_DEMO/LIVE, CRYPTO_PAPER/TESTNET/LIVE), never summed | Implemented+tested |
| 5.1 Lifecycle events | Implemented+tested |
| 5.3 Score (20/25/20/15/10/10) with evidence; thresholds configurable (`assessment` setting) | Implemented+tested. Whether the score predicts outcomes is **unknown** until enough shadow outcomes exist |
| 5.4 Hard gates (data, confirmed candle, stop/target, net R:R ≥ 1, calendar, risk engine incl. min size, limits, duplicates, kill switch) | Implemented+tested |
| 5.5 AI: read-only; prompt requires OBSERVED/INTERPRETATION/UNCERTAINTY; model test button | Implemented. The JSON-schema-validated structured AI verdict is **not built** (AI is optional and never required for a trade) |
| 6 Economics: net stop/target incl. commission + slippage, gross/net R:R, % of account, break-even win rate, min-size infeasibility message | Implemented+tested |
| 6 p(win) only from ≥ 30 resolved comparable outcomes, otherwise UNKNOWN; reliability labels | Implemented+tested |
| 6.3 Verdicts NO_TRADE / WATCH / PAPER_TEST / RESEARCH_SUPPORTED + "do nothing" | Implemented+tested |
| 7 Alert center (inbox, dedup, retries ≤ 3, delivery log, Telegram, SMTP email, per-type preferences, daily summary, weekly review) | Implemented. In-app alerts tested; Telegram/email delivery **not verified** (no credentials here) |
| 7 WebSocket push / browser push | **Not built.** The inbox polls every 15 s |
| 8 Backtest provenance (code commit, config/data/result hashes), R confidence interval, 60/20/20 split, baseline vs challenger | Implemented. Reproducibility of the hash is by construction, not separately tested |
| 9 Shadow mode: every signal (rejected/expired included) followed to target/stop/expiry; conservative same-candle rule | Implemented+tested |
| 9 Paper ledger reconciliation with incidents/alerts | Implemented (paper forex ledger). The crypto paper ledger is not yet reconciled |
| 10.1 Pilot preset (0.25 %, 1 %/3 %, 1 position, 3 trades/day, approval on) and max trades/day check | Implemented+tested. **Not applied automatically**: press "Apply pilot limits" |
| 10.2 Live gate: server-side, evidence-based (backtest final test, ≥ 50 trades over ≥ 28 days, self-test ≤ 7 days old, 2FA, written notes), expires after 30 days, auto-drops BROKER LIVE to DEMO on expiry, also required for crypto LIVE | Implemented+tested. The old checkbox-only approval is no longer valid (it had no expiry), so live is **locked** after this deploy |
| 11 Broker audits (Capital.com, cTrader, MT5, crypto) | **Not verified** against real accounts in this session |
| 14 Dashboard trust cards, NO TRADE banner, signal tabs, signal detail (gain vs loss, comparable stats, gates, lifecycle, outcome), alerts page, monthly/funnel/compare | Implemented; browser-tested on simulated data |
| 16 Privacy mode (blurs names/account ids for screenshots) | Implemented. **Rotate the test password that was shared in chat** — the owner must do this |

## 4. Tests
Backend: 63 integration tests and 35 unit tests (including new `test_trust.py`). Capital connector: 20 tests. cTrader connector: 6 tests. Browser screenshots were taken of the dashboard, signal board/detail, alerts, risk/live gate and trades pages, using simulated prices.

## 5. Open risks / next steps
1. Moving money fields to NUMERIC needs Alembic; it was not done in place.
2. Verify the Capital.com spread during London hours on `/api/data/health`.
3. Set up Telegram (Alerts page) and press "Send test".
4. Collect ≥ 30 resolved signals per strategy before trusting any win rate. Expect weeks.
5. Live remains locked; the evidence items show exactly what is missing.
