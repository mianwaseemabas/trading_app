# Qivynex — Real-World Trading Improvement Master Plan

**Version:** 2.0 | **Date:** 2026-10-31 | **Target:** Existing https://trade.qivynex.com application | **Deployment:** Existing Ubuntu 24.04 VPS

> **Mission:** Turn Qivynex from a feature-rich trading dashboard into a trustworthy decision-support, signal-alert, paper-trading and evidence-based performance platform. The aim is to help its owner make better financial decisions with limited capital, not to promise daily income or automatic profits.

## 0. Instructions to the implementation agent

This is a **brownfield enhancement**. Inspect the current repository, schema, migrations, tests, running services and existing endpoints first. Preserve working features and historical data. Produce an architecture diff, phased implementation plan, database backup, reversible migrations, automated tests and release notes. Do not rewrite the application or change live-mode permissions by default. Use mock providers when credentials or access are unavailable. Do not invent results, API availability, regulatory eligibility or profitability.

**Absolute rule:** BROKER LIVE remains disabled until all safety gates, tests, paper evidence and explicit owner authorization are complete. No LLM can submit orders or bypass risk limits.

## 1. The actual objective: meaningful progress, not fantasy returns

Starting capital is approximately **$500**. At this size, spreads, minimum lot sizes and losses matter enormously. A hypothetical 2% monthly return equals $10 before costs, and returns may be negative. Do not build dashboards that imply a reliable daily wage, guaranteed income or calibrated win probability where none exists. The platform should instead measure whether a repeatable, cost-adjusted advantage exists.

**Primary success criteria:** capital preserved; no unauthorized live trades; every signal has traceable evidence; accurate risk estimates; no stale-price trading; paper results match realistic execution assumptions; strategy shows positive out-of-sample net expectancy with uncertainty reported. If these conditions fail, **NO TRADE** is the correct result.

**Personal operating rules:** Never use money needed for living expenses, bills, emergencies or debt repayment. Do not add money to recover losses. Do not average down or use martingale/grid recovery. Avoid leverage initially. Do not set a daily profit target that encourages forced trades. Review progress monthly, not after every candle.

## 2. What exists already — screenshot-based review

The owner supplied 11 screenshots. This is a **UI review only**, not a source-code, security, provider or execution audit.

| Existing Qivynex feature | Screenshot evidence | Expert action |
|---|---|---|
| Dashboard and terminal | $500 practice equity, P&L, quotes, market intelligence | Retain; add data trust status, net-cost metrics and risk-first summary |
| Trading modes | OFF, SIGNALS ONLY, PRACTICE, BROKER DEMO, BROKER LIVE | Retain; enforce server-side state machine and hard live lock |
| Strategy controls | EMA pullback baseline and SMC sweep→BOS/CHoCH→retest option | Retain; version, compare and validate both before promoting |
| Market overview | H4/H1/M15 alignment, ATR, structure, liquidity | Retain; use as signal evidence and warn on stale/expensive quotes |
| SMC scanner | Swings, BSL/SSL, BOS/CHoCH, sweeps, FVG, displacement | Retain; verify no repaint/look-ahead; persist detections |
| Charting | Candles, EMA, RSI, MACD and ATR | Retain; overlay actual signal entry, invalidation, target and outcome |
| Broker page | Capital.com demo, cTrader fields, MT5 bridge, crypto spot | Audit contracts, credentials, demo/live segregation and reconciliation |
| Risk & Strategy | Stop mandatory, loss limits, spread/slippage, kill switch | Retain; test controls end-to-end and fix policy inconsistencies |
| Setups to approve | Manual approval with expiry and recheck | Retain; add detailed evidence, cost and risk preview before approval |
| AI Assistant | Educational explanations; provider/model settings | Retain; make AI a read-only evidence interpreter |
| Guide, audit, backtest, trades, news | Navigation and documented workflows | Audit actual behavior; integrate measurable outcomes |

### High-priority findings

1. **Unusually wide Forex spreads in screenshots:** EURUSD ~7 pips, GBPUSD ~9 pips, USDJPY ~8 pips. The dashboard shows bid/ask differences consistent with these displays, but the root cause is **unverified**. Quotes were shown around a weekend/market-close period. Diagnose timestamp, trading session, quote source, pip-vs-point conversion and broker conditions. Do not trade these spreads blindly.
2. **Mode/balance ambiguity:** $500 application paper account, a Capital.com demo balance near $10,917, and a crypto practice balance near $99.85 appear in different panels. Show explicit account, currency, source, environment and equity for every metric. Never combine balances across modes or accounts.
3. **Live checklist appearance:** all five items appear checked and an approval indicator is visible, while BROKER LIVE is not active. Treat this as a verification requirement, not proof of unsafe live execution. Require verifiable test artifacts and an independently enforced server-side live gate.
4. **Risk policy consistency:** screenshots show 2% daily/$10 and 4% weekly/$20 for a $500 reference balance. These are consistent mathematically, but may be too permissive for an early pilot. Proposed stricter *test* limits: 1% daily ($5), 3% weekly ($15), 0.25–0.5% per trade ($1.25–$2.50). Verify whether the actual broker minimum position size can respect them.
5. **Strategy trading is enabled while approval-before-trade is off** in the pictured practice interface. Consider defaulting to manual approval for every new strategy during early testing. Ensure controls are scoped per market, strategy and account.
6. **Activity shows recent paper crypto trades with small negative P&L.** Audit fill pricing, fees, order quantity units, stop protection and paper-equity accounting. Do not infer strategy performance from a few trades.
7. **SMC event log panel shows no logged events** despite chart detections. Investigate event persistence and asynchronous detection logging. This is a candidate observability gap, not a confirmed backend defect.
8. **AI provider model field appears custom-entered.** Validate configured model identifiers and API responses rather than assuming a displayed string corresponds to a working supported model.
9. **Screenshots show an email address and broker account identifier.** Redact personal/account details from demos and screenshots; rotate the test login password shared in chat. Never include credentials in this document or code.

## 3. Product redesign: seven decisions the platform must answer

For every candidate setup, Qivynex should answer:

1. **What is happening?** Market, regime, timeframe and evidence.
2. **Why this signal?** Deterministic strategy rules and AI explanation with conflicts.
3. **Is the data trustworthy?** Provider, quote age, spread, market session, calendar freshness.
4. **How much could I lose?** Stop distance, quantity, fees, slippage, worst-case caveats.
5. **How much could I gain?** Target scenarios, net reward-to-risk, no guarantees.
6. **Is it worth taking?** Historical *net* expectancy by comparable regime, sample size, uncertainty, opportunity cost and risk budget.
7. **What happened afterward?** Triggered, missed, expired, stopped, targeted, cancelled; realized paper/broker outcome.

**No-trade is a first-class recommendation.** Build a visible reason list and record avoided trades for research without claiming every avoided trade was a win.

## 4. Priority 0: trust and data correctness

### 4.1 Unified instrument and quote service

Each quote must include `provider`, `account_environment`, `instrument_id`, `asset_class`, `bid`, `ask`, `timestamp_provider`, `timestamp_received`, `session_state`, `quote_age_ms`, `pip_size`, `tick_size`, `contract_size`, `min_qty`, `qty_step`, `quote_currency`, `conversion_rate_source`, `data_quality`.

Use `Decimal`/SQL `NUMERIC` for money and prices. Use UTC and explicit timezone conversion in UI. Preserve raw provider payload reference for debugging. Enforce instrument-specific pip/point calculations (USDJPY differs from EURUSD; crypto spot uses base-asset quantity, not Forex lots).

### 4.2 Spread quality gate

Compute `spread = ask - bid`, `spread_pips = spread / pip_size` for Forex and `spread_bps = 10000 * spread / mid` where relevant. Define instrument/session-specific thresholds and relative spread-to-ATR. Mark closed-market quotes and suppress entry alerts when executable liquidity is unavailable. Do not replace missing bids/asks with last trade price.

### 4.3 Data incident handling

If feed stale, inconsistent, disconnected, outside session or spread too high: suppress order eligibility, mark signal `BLOCKED_DATA`, alert operator once per incident, log recovery. Require completed candles for closed-candle strategies. No silent interpolation or invented candle volume.

### 4.4 Account ledger segregation

Separate `APP_PAPER`, `BROKER_DEMO`, `BROKER_LIVE`, `CRYPTO_PAPER`, `CRYPTO_TESTNET`, `CRYPTO_LIVE` in both DB and UI. Each balance must show source and last update. No cross-account P&L aggregation without explicit user selection and currency conversion.

## 5. Priority 1: the Signal Intelligence Center

### 5.1 Signal lifecycle

`DETECTED → DATA_VALIDATED → SCORED → AI_REVIEWED → WATCHLIST/CANDIDATE/REJECTED → RISK_ELIGIBLE → ALERTED → AWAITING_APPROVAL → APPROVED/DECLINED/EXPIRED → PAPER_ORDERED → FILLED/PARTIAL/CANCELLED → CLOSED → EVALUATED`.

Track separate *signal status* and *order status*. Signal approval never means an order is guaranteed; revalidate market data, entry, stop, broker and risk at submission.

### 5.2 Deterministic evidence

- H4 context, H1 trend/structure, M15 entry; optional M5 refinement only after validation.
- EMA 20/50/200, RSI 14, MACD 12/26/9, ATR 14, ADX and meaningful volume where available.
- Confirmed swing pivots, BOS, CHoCH, liquidity sweeps, FVG and mechanically defined order-block *candidates*.
- Session, volatility regime, news blackout and instrument-specific spread.
- Record evidence timestamp and when it became knowable; no future candles or repainting.

### 5.3 Initial scoring (hypothesis, not win probability)

| Component | Points |
|---|---:|
| H4/H1 trend alignment | 20 |
| Confirmed structure/liquidity | 25 |
| Independent quantitative confirmation | 20 |
| Entry quality and feasible net reward/risk | 15 |
| Volatility, liquidity and volume | 10 |
| Event/session conditions | 10 |

0–59 reject; 60–69 watch; 70–79 candidate; 80–100 *eligible for risk review*. These thresholds are configurable hypotheses. Test whether they predict outcomes; avoid double-counting correlated trend indicators. A score of 84 **does not** mean 84% probability of profit.

### 5.4 Hard rejection gates

- Stale or incomplete data, invalid contract metadata, market closed.
- Spread/slippage too high; calendar stale when required; event blackout.
- No valid stop or target, invalid entry, negative/insufficient net R:R.
- Minimum order size violates risk budget; available balance unknown.
- Exposure, daily/weekly loss, trade count or kill switch breached.
- Duplicate/expired signal, already-filled equivalent order, unconfirmed candle.
- Execution venue unsupported, broker state uncertain, provider outage.

### 5.5 AI role

The AI assistant may summarize supplied facts, explain contradictory evidence, rank research questions and produce structured `candidate/watch/reject` commentary. It may **not** fabricate price levels, alter score inputs, determine position size, override hard gates or place trades. Validate JSON schema; keep provider/model/prompt version and source evidence. If AI is required but unavailable, default to no new trade.

## 6. Priority 1: realistic gain/loss and investment-worthiness panel

Every signal detail must show:

- Bid/ask and quote timestamp; side of market used for entry and exit.
- Planned entry, stop, target 1 and optional target 2; invalidation reason.
- Position size with instrument-specific units and minimum increment.
- Gross target gain, expected fees/spread/slippage, **estimated net target gain**.
- Planned stop loss **including estimated costs**, and warning that gaps/slippage can exceed it.
- Gross and net R:R; percentage of account at risk.
- Break-even win rate implied by net average win/loss assumptions.
- Historical strategy win rate, profit factor, drawdown, expectancy and sample size by comparable instrument/regime/session.
- Reliability label: `INSUFFICIENT_EVIDENCE`, `RESEARCH_ONLY`, `PAPER_CANDIDATE`, `PAUSED`, never `GUARANTEED`.

### 6.1 Mathematical contract

`risk_budget = account_equity * risk_fraction`

`gross_target = quantity * price_value_per_unit * abs(target - entry)` (instrument-specific)

`gross_stop = quantity * price_value_per_unit * abs(entry - stop)` (instrument-specific)

`net_target_est = gross_target - estimated_round_trip_costs`

`net_stop_est = gross_stop + estimated_round_trip_costs`

`net_RR = net_target_est / net_stop_est` when denominator positive.

`net_expectancy = p_win * avg_net_win - (1 - p_win) * avg_net_loss`.

`break_even_win_rate = avg_net_loss / (avg_net_win + avg_net_loss)` when both positive.

**Never derive p_win from the signal score.** Use actual comparable out-of-sample or paper outcomes and show sample size/uncertainty. If insufficient, display **UNKNOWN** rather than invent a probability. For multi-target trades, calculate weighted scenario outcomes and partial-close fees explicitly.

### 6.2 $500 account examples — illustrative only

At 0.5% risk, planned risk is $2.50. A 2R gross target is $5.00 before costs. If total estimated round-trip cost is $0.50, net target is ~$4.50 and net stopped loss is ~$3.00 under simplified assumptions. The true sizing calculation must ensure the **total planned loss including costs** stays within $2.50, otherwise reduce size or reject. Forex minimum lot sizes may make this impossible. Do not suggest unrealistic micro-position sizing.

### 6.3 Worth-it decision engine

Return a structured verdict with **reasons**, not an investment guarantee:

- `NO_TRADE`: invalid data, unacceptable cost, excessive risk, weak evidence or missing validation.
- `WATCH`: technically interesting but no trigger or insufficient confirmation.
- `PAPER_TEST`: setup passes deterministic checks but profitability is not validated.
- `RESEARCH_SUPPORTED`: sufficient out-of-sample evidence exists for controlled paper evaluation; not a promise of future returns.

Include comparison to `DO_NOTHING` as a legitimate choice. Do not automatically recommend a live trade based on a positive historical average.

## 7. Priority 1: actionable alert center

### 7.1 Alert types

`SIGNAL_DETECTED`, `SIGNAL_QUALIFIED`, `SIGNAL_REJECTED`, `SIGNAL_EXPIRED`, `ENTRY_NEAR`, `ENTRY_TRIGGERED`, `APPROVAL_REQUIRED`, `APPROVAL_EXPIRING`, `ORDER_SUBMITTED`, `ORDER_FILLED`, `PARTIAL_FILL`, `STOP_HIT`, `TARGET_1_HIT`, `TARGET_2_HIT`, `TRAILING_STOP_UPDATED`, `POSITION_CLOSED`, `LOSS_LIMIT_REACHED`, `SPREAD_TOO_WIDE`, `DATA_STALE`, `BROKER_DISCONNECTED`, `KILL_SWITCH_ACTIVE`, `DAILY_SUMMARY`, `WEEKLY_REVIEW`.

### 7.2 Delivery channels

1. In-app notification center and WebSocket updates.
2. Email with compact details.
3. Telegram bot as optional opt-in (never send API secrets).
4. Browser push later, after permission and delivery reliability are tested.

Store delivery attempts, failures, deduplication key, retry policy and user acknowledgments. Prevent alert storms using throttling and state transitions. Alerts are **not** orders.

### 7.3 Alert template

```text
QIVYNEX | PAPER SIGNAL — EURUSD BUY
Status: Candidate / awaiting confirmation
Strategy: hybrid_smc_trend v1.0
Score: 82/100 (quality score, not win probability)
Data: provider, time, quote age, spread
Entry: calculated price or 'not triggered'
Stop: calculated invalidation level
Target: calculated target
Planned account risk: $X (Y%)
Estimated net gain at target: $A
Estimated loss at stop: $B; can exceed due to gaps/slippage
Net R:R: N.N
Reason: H1 trend + confirmed M15 sweep and structure shift
Warnings: event/spread/uncertainty
Action: Review in Qivynex (no automatic live order)
```

Only send populated fields when actual validated values exist; otherwise say `unavailable`.

## 8. Priority 2: backtesting that can be trusted

Use event-sequenced historical simulation with no future information. Confirm pivots only when right-side candles have closed. Model bid/ask, variable spreads, commissions, slippage, session gaps, minimum lot sizes, broker precision, rejected orders, stop/limit fills and crypto fees. Use conservative assumptions when tick data is unavailable; do not assume every touched limit fills.

- Split chronologically into development, validation and untouched test sets.
- Perform walk-forward testing and compare to simple baselines.
- Report results by strategy, version, symbol, market regime, session, direction and volatility.
- Show net expectancy in $ and R, profit factor, drawdown, average win/loss, trade count, fees and confidence intervals where possible.
- Use multiple periods and market conditions. A 50–100-trade sample can reveal issues but is not proof of profitability.
- Save full run config, code commit, data source, date range, cost model, trade list and result hash.
- Implement `strategy_challenger` testing: compare baseline EMA pullback vs SMC strategy with identical costs and periods; promote only on out-of-sample evidence.
- Do not retune repeatedly on the untouched test set.

## 9. Priority 2: paper-trading verification

- Separate APP_PAPER and CRYPTO_PAPER ledgers; broker demo is separate.
- Simulate order states: CREATED, ACCEPTED, PARTIAL, FILLED, CANCELLED, EXPIRED, REJECTED.
- Track expected vs actual simulated fill, fees, spread, stop/target outcomes, equity and balance.
- Reconcile every paper order to positions and ledger entries; expose mismatch incidents.
- Store all signals, **including rejected and expired signals**, for unbiased analysis.
- Add `shadow mode`: run a strategy and record hypothetical entries without placing even paper orders; compare to active paper execution.
- Review at least 4–8 weeks across varied conditions where feasible, and target a sufficiently large sample for each strategy. Do not treat a calendar duration alone as proof.

## 10. Risk engine and account protection

### 10.1 Proposed initial configuration (paper pilot)

| Control | Suggested test default |
|---|---|
| Equity reference | Actual active account equity, not a global $500 constant |
| Per-trade risk | 0.25% initially; upper pilot cap 0.5% |
| Daily loss stop | 1% |
| Weekly loss stop | 3% |
| Maximum concurrent positions | 1 initially |
| Maximum new trades/day | 3 |
| Initial markets for strategy validation | EURUSD and GBPUSD research; choose one for first controlled pilot |
| Futures/leverage | Off initially |
| Stop-loss | Mandatory, broker-side when supported |
| Live trading | Locked |

Recalculate limits from actual account equity with documented period anchors and include unrealized losses according to policy. Avoid accidental resets on restart/timezone changes. Reject when minimum lot size or spread makes risk infeasible. Verify broker leverage, margin and contract details independently.

### 10.2 Safety behavior

- Idempotency keys on every order; query broker after timeout before retry.
- Server-side authorization and live gate independent of frontend toggles.
- Kill switch stops **new** orders immediately; existing position liquidation follows a separately documented policy.
- Rate limits, exposure caps, correlated positions, cooldown after losses and manual review after circuit breaker.
- Reconciliation after every reconnect and VPS restart.
- A high signal score never overrides risk gates.

## 11. Broker and integration audit

### Capital.com

Verify demo environment, authentication/session renewal, instrument IDs, bid/ask, lot/size semantics, minimum size, contract currency, stop rules, broker fees, order status, margin and account reconciliation. Keep live keys and demo keys segregated.

### cTrader

Verify OAuth scopes/token rotation, account selection, volume units, market hours and partial fills. Do not mark connected until a real health check succeeds.

### MT5

The screenshot describes a Windows MT5 bridge. Document host/bridge security, authenticated channel, reconnection, queue durability and duplicate-order handling. Do not assume Ubuntu-only MT5 Python terminal integration.

### Crypto spot

Use exchange-specific filters for step size, minimum notional, quote/base asset quantities, fees, order types and stop-order support. The screenshot says stops are immediately placed and assets sold if protection fails: verify this behavior with controlled test cases, including API failure and partial fills. Do not claim guaranteed protection.

### Equities

Do not claim equities execution exists from screenshots. Add an adapter only after provider eligibility, entitlements, market hours, corporate actions and order semantics are confirmed.

## 12. API extension proposal (adapt to existing routes)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/v1/data/health` | Provider latency, freshness, spread incidents |
| GET | `/api/v1/accounts` | Segregated paper/demo/live account states |
| GET | `/api/v1/signals` | Filterable signal list |
| GET | `/api/v1/signals/{id}` | Evidence, score, risk, status, outcomes |
| POST | `/api/v1/signals/scan` | Queue authorized scan; idempotent job |
| POST | `/api/v1/signals/{id}/review` | Approve/decline with fresh revalidation |
| GET | `/api/v1/signals/{id}/economics` | Estimated gain/loss/costs and uncertainty |
| GET | `/api/v1/strategies/compare` | Baseline/challenger results |
| POST | `/api/v1/backtests` | Queue versioned backtest |
| GET | `/api/v1/backtests/{id}` | Backtest results and provenance |
| GET | `/api/v1/performance` | Net strategy performance |
| GET | `/api/v1/alerts` | Alert inbox |
| POST | `/api/v1/alerts/preferences` | Channel and frequency preferences |
| GET | `/api/v1/risk/status` | Exposure, limits, kill switch, live gate |
| GET | `/api/v1/audit/events` | Authorized audit queries |

Use existing auth/RBAC, pagination, schema validation, CSRF defenses where relevant, rate limits and structured audit logs. Order endpoints must have idempotency and broker-state revalidation.

## 13. Database additions (migrate, do not replace)

- `provider_health_events` — freshness, spreads, disconnections.
- `account_environments` — account/source/currency/mode separation.
- `instrument_contract_specs` — precision, min size, pip/point, contract and version.
- `strategy_versions` — immutable parameter snapshots and code commit.
- `signal_evidence` — rule results and confirmation timestamps.
- `signal_score_breakdowns` — component points and version.
- `signal_lifecycle_events` — transitions with reasons.
- `signal_economics` — entry/stop/target, quantity, fees, risk, scenarios.
- `signal_outcomes` — target/stop/expiry, observed price path, cost-adjusted result.
- `alert_rules`, `alert_events`, `alert_deliveries`, `notification_preferences`.
- `backtest_runs`, `backtest_trades`, `validation_splits`.
- `paper_ledger_entries`, `reconciliation_incidents`.
- `live_gate_evidence` — checklist evidence, reviewer, expiry, approval audit.

Keep immutable audit trails, external IDs, UTC, Decimal financial types, unique constraints and indices. Never store plaintext credentials.

## 14. Dashboard/UI changes — incremental

### Dashboard

Add four prominent cards: `DATA HEALTH`, `RISK AVAILABLE`, `QUALIFIED SIGNALS`, `NET PERFORMANCE (PAPER)`. Clearly label account environment and last update. Show a **NO TRADE** banner with specific reasons when markets are unsuitable.

### Signals

Add tabs `All`, `Qualified`, `Watch`, `Rejected`, `Expired`, `Completed`. Each row: instrument, direction, setup, score, spread, data age, entry/stop/target, net R:R, max planned loss, alert state and reason. Click for full evidence and chart overlays.

### Signal detail

Show **Potential gain vs planned loss** side-by-side, net of costs; emphasize uncertainty. Show actual comparable strategy performance with sample size, not a fabricated success probability. Provide `Save to watchlist`, `Set alert`, `Paper test`, `Decline` and `Explain` actions; no live-order button until gates pass.

### Alerts

Add notification inbox with read/unread, channel preferences, event timeline, delivery status and deduplication. Default to qualified signals and risk incidents, not noisy every-candle messages.

### Performance

Add monthly P&L, equity curve, drawdown, win/loss distribution, net expectancy, spread/fee costs, results by market regime, and signal-to-execution funnel. Show $ and R. Always distinguish paper, broker demo and live.

### Risk & Strategy

Add effective-policy preview (including account), strategy-version comparison, validated evidence links for live gate, and an explanation of why an order was blocked. Preserve kill switch and hard stop requirements.

## 15. Suggested implementation phases and acceptance tests

### Phase 0 — Audit and safety (do first)

**Build:** repository inventory, service map, database backup, environment segregation, rotate shared test credentials, verify live lock, instrument specs and spread calculation.

**Pass when:** all mode/account balances are clearly separated; quote diagnostics explain spread; no unapproved live path; backups restore successfully.

### Phase 1 — Data trust and signal evidence

**Build:** normalized quotes, data-health gate, persistent SMC events, strategy versions, signal evidence, hard rejection reasons.

**Pass when:** deterministic fixtures produce repeatable signals and all stale/invalid inputs fail closed.

### Phase 2 — Economics and decision support

**Build:** position sizing, net gain/loss calculator, net R:R, cost estimate, `NO_TRADE`/`WATCH`/`PAPER_TEST` verdicts.

**Pass when:** calculations match independently checked broker/instrument examples and minimum-size violations are rejected.

### Phase 3 — Alerts

**Build:** in-app notifications, email, optional Telegram, dedupe, retry, user preferences, delivery logs.

**Pass when:** alerts arrive for qualified/expired/filled/stopped/target events without duplicates, and outages are visible.

### Phase 4 — Reliable research

**Build:** backtest audit, realistic fills, out-of-sample/walk-forward reports, baseline-vs-SMC comparison, performance dashboard.

**Pass when:** same data/config/code produces reproducible results; look-ahead and optimistic fill tests pass.

### Phase 5 — Paper validation

**Build:** paper reconciliation, shadow mode, daily/weekly review, incident monitoring.

**Pass when:** order/position/ledger reconciliation is stable and strategy performance is measured net of costs over varied conditions.

### Phase 6 — Optional controlled live pilot (separate owner decision)

**Build:** only after independent review of legal/provider eligibility, risk tests, credentials, broker reconciliation, paper evidence and explicit authorization.

**Pass when:** one selected instrument, smallest feasible size, strict risk caps, broker protective-order behavior and emergency shutdown have all been verified. Passing does not guarantee profitability.

## 16. Security and reliability requirements

Ubuntu VPS: SSH keys, UFW, HTTPS, restricted Docker networks, no public DB/Redis, dependency patching, encrypted backups with restore drills, secret rotation, RBAC and MFA for admin, encrypted provider tokens, structured logs, request IDs, health checks and alerting. No API keys in frontend bundles, screenshots, AI prompts or source control. Audit every risk-policy and mode change. Add recovery behavior for VPS restart, stale data, AI failure, broker disconnect, duplicate webhook and uncertain order submission.

## 17. Weekly owner workflow (15–30 minutes)

**Daily:** check data health and high-impact events; inspect only qualified signals; never force a trade; record reason for each approved/declined paper setup; stop when risk or emotional discipline is compromised.

**Weekly:** review every completed paper signal and cost-adjusted outcome; compare expected vs actual fills; inspect losses and rule violations; disable weak strategies rather than adding risk; document one improvement hypothesis at a time.

**Monthly:** compare out-of-sample and paper results, costs and drawdown; ask whether the system has demonstrated an advantage. If not, keep live mode off. Progress can mean improved process and smaller losses, not just positive P&L.

## 18. What NOT to build yet

- “Guaranteed daily earnings” or fixed income target widgets.
- Win-probability badges derived from arbitrary signal scores.
- Unrestricted AI order execution or self-modifying live strategies.
- Martingale, averaging down, recovery grid or automatic leverage escalation.
- Expensive paid data subscriptions without a demonstrated need.
- Social-copy trading or anonymous paid signal subscriptions.
- Complex multi-agent orchestration before basic data and risk tests pass.
- Simultaneous live launch of Forex, stocks and crypto.

## 19. Definition of done

- [ ] Existing application and data preserved.
- [ ] Provider data freshness and spread calculation verified.
- [ ] All account environments and P&L clearly segregated.
- [ ] SMC/quant evidence reproducible with no future information.
- [ ] Every signal has reasons, costs, risk, score breakdown and lifecycle history.
- [ ] No-trade decisions are visible and auditable.
- [ ] Gain/loss calculator respects real contract specs and minimum sizes.
- [ ] Alert delivery and deduplication tested.
- [ ] Backtests include fees, spread, slippage and out-of-sample reporting.
- [ ] Paper fills and ledger reconcile; failures raise incidents.
- [ ] AI cannot submit orders, change risk or invent numeric evidence.
- [ ] Live mode is disabled by default and requires verified human approval.
- [ ] Kill switch, order idempotency, timeout recovery and broker reconciliation tested.
- [ ] Secrets secured, backups restored and admin MFA enabled.
- [ ] User guide explains each signal, each risk and how to stop the system.

## 20. Required handoff from the coding agent

Deliver: (1) architecture audit, (2) files changed, (3) migrations and rollback, (4) APIs and schemas, (5) screenshots of updated UI, (6) automated test results, (7) provider limitations, (8) paper-trading validation report, (9) security and live-gate evidence, (10) open risks and next steps. Clearly distinguish implemented, tested, simulated and not yet verified features.

**Closing principle:** Qivynex should help the owner protect scarce capital and learn whether any real, repeatable edge exists. If the best trade is no trade, the system must say so plainly. Small improvements in financial judgment matter more than impressive-looking signals.
