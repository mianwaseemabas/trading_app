# AI Forex Trading System --- One-Time Implementation Plan

**Project status:** Planned\
**Initial capital:** USD 500\
**Market:** Forex currency pairs only\
**Trading platform:** To be selected; MetaTrader 5 (MT5) is the
preferred starting candidate\
**Automation target:** Fully automated execution after testing and
explicit activation\
**Execution approach:** One-time project implementation, followed by
user-operated monitoring (no scheduled or recurring task is being
created)

------------------------------------------------------------------------

## 1. Project objective

Build a personal AI-assisted Forex trading system that combines an
established trading platform with a separately developed AI application.

The system should:

1.  Collect market data from a supported broker/platform.
2.  Analyze currency pairs using transparent, testable quantitative
    strategies.
3.  Generate and record trading signals.
4.  Apply strict, deterministic risk checks.
5.  Execute trades through a supported broker connection only when all
    checks pass.
6.  Provide a web dashboard, trade history, logs, and notifications.
7.  Support historical backtesting and demo trading before any live
    deployment.

**Important:** The system is not expected or guaranteed to be
profitable. AI predictions can be wrong, and automated trading can lose
some or all of the funds deposited. The USD 500 amount is a proposed
maximum initial trading balance, not a development budget or a target to
spend.

## 2. Recommended platform

### Preferred initial candidate: MetaTrader 5

MT5 is a practical first candidate because it supports demo accounts,
Expert Advisors, historical data, and a Python integration.

-   Official site: https://www.metatrader5.com/
-   Broker selection must happen before live integration.
-   Confirm that the broker supports the required MT5 features, account
    type, instruments, minimum lot size, and API/terminal access.

### Optional supporting platform: TradingView

TradingView may be used for chart review and alerts, but it is not
required for the first MVP.

-   Official site: https://www.tradingview.com/

### Broker due diligence

Before opening or funding an account:

-   Verify the broker's legal authorization and eligibility to serve a
    Saudi resident.
-   Check the relevant Saudi regulatory guidance, including the Capital
    Market Authority: https://cma.org.sa/
-   Confirm the exact product being offered (for example, rolling spot
    forex or CFDs), leverage, spreads, commissions, swap/overnight
    financing, margin close-out rules, withdrawal terms, and minimum
    trade size.
-   Do not assume an overseas licence automatically authorizes
    solicitation or provision of services in Saudi Arabia.
-   Do not deposit funds until these checks are complete.

## 3. Proposed architecture

``` text
Forex broker / MT5
       |
       v
Market-data and account gateway
       |
       v
Python backend (FastAPI)
       |
       +--> Market data service
       +--> Indicator / feature calculation
       +--> Strategy evaluation
       +--> News and event context (optional)
       +--> Deterministic risk engine
       |
       v
Trade authorization and execution service
       |
       v
MT5 terminal / broker order execution
       |
       +--> PostgreSQL trade and event records
       +--> Web dashboard (React / Next.js)
       +--> Telegram notifications (optional)
```

### Hosting arrangement

Recommended arrangement:

-   **Ubuntu 24.04 VPS:** FastAPI backend, database, background jobs,
    dashboard, monitoring, and logs.
-   **Windows VPS or Windows machine:** MT5 desktop terminal and its
    Python integration / execution adapter.
-   Connect the two environments using a private, authenticated,
    encrypted API channel. Restrict firewall access to known endpoints
    and use strong secrets.
-   If the current Ubuntu VPS has adequate resources, reuse it for the
    backend. Do not assume it can run the MT5 desktop terminal natively.

## 4. Technology stack

  Component               Proposed technology
  ----------------------- ------------------------------------------------
  Trading terminal        MetaTrader 5
  Execution adapter       Python + MetaTrader5 package on Windows
  Backend API             Python + FastAPI
  Data processing         Pandas + NumPy
  Strategy research       Python; scikit-learn/PyTorch only if justified
  Database                PostgreSQL
  Background processing   Redis + Celery, if needed
  Dashboard               React / Next.js
  Notifications           Telegram Bot API, optional
  AI language model       OpenAI API or Claude API, optional
  Hosting                 Ubuntu VPS + Windows execution host

Keep the first version simple. Do not add machine learning, multiple LLM
agents, or complex orchestration until a baseline strategy and data
pipeline work reliably.

## 5. Agent and service responsibilities

Treat these as separate services or modules. An LLM must never be the
final authority for trade sizing or risk approval.

### A. Market data service

-   Fetch bid/ask quotes and candle data for approved currency pairs.
-   Validate timestamps, missing candles, stale quotes, and abnormal
    values.
-   Record source and collection time.
-   Stop signal generation when data is stale or incomplete.

### B. Technical analysis service

-   Calculate indicators such as EMA 50, EMA 200, RSI 14, MACD, and ATR
    14.
-   Use closed candles for decisions unless a strategy explicitly and
    safely handles intrabar data.
-   Produce structured, reproducible features and signal explanations.

### C. Strategy service

-   Start with one documented strategy.
-   Generate BUY, SELL, or NO-TRADE proposals.
-   Include instrument, timeframe, entry condition, stop-loss logic,
    take-profit logic, and strategy version.
-   Do not treat an indicator combination as proven profitable without
    testing.

### D. News and event context (optional)

-   Identify scheduled high-impact economic releases from a reputable
    data source.
-   Initially use this service as a trading restriction / context
    filter, not as an LLM-generated price prediction.
-   If reliable event data is unavailable, disable the filter and
    clearly log that status.

### E. Risk engine

-   Independently calculate position size from account equity, stop
    distance, contract specifications, and account currency.
-   Enforce per-trade, daily, weekly, exposure, and margin limits.
-   Reject trades if stop-loss is missing, position size is below/above
    broker limits, data is stale, or any required check fails.
-   Provide a global kill switch.
-   Never allow an LLM or strategy module to override risk limits.

### F. Execution service

-   Accept only signed/validated trade instructions from the backend.
-   Check current spread, market status, margin, duplicate orders, and
    order parameters immediately before submission.
-   Place protective stop-loss orders as part of the trade workflow
    where supported.
-   Verify broker acknowledgement and actual filled volume/price.
-   Reconcile local records with broker positions and orders.
-   Fail closed: if validation, connectivity, or reconciliation fails,
    do not open a new trade.

### G. Reporting service

-   Record signals, rejected trades, orders, fills, fees, swaps, exits,
    and errors.
-   Calculate performance after costs.
-   Send optional Telegram notifications.
-   Never report an unrealized gain as a realized profit.

## 6. Initial strategy research scope

Start with a small, manageable universe:

-   EUR/USD
-   GBP/USD
-   USD/JPY

Initial research timeframes: 15-minute and 1-hour charts. Compare
results rather than assuming one timeframe is superior.

Potential indicators:

  Indicator            Initial use
  -------------------- ---------------------------------------
  EMA 50 and EMA 200   Trend context
  RSI 14               Momentum context
  MACD                 Momentum / trend confirmation
  ATR 14               Volatility and stop-distance research

Example hypothesis to test (not a validated trading system):

-   Consider long setups only when EMA 50 is above EMA 200.
-   Consider short setups only when EMA 50 is below EMA 200.
-   Use RSI and MACD as additional filters.
-   Define entry and exit rules precisely before testing.
-   Set stop distance using a documented rule that accounts for
    volatility.
-   Skip trades when spread, volatility, or market conditions exceed
    configured limits.

Do not optimize dozens of parameters on the same historical sample. Use
out-of-sample testing and walk-forward evaluation to reduce overfitting.

## 7. Initial risk policy for a USD 500 demo/live test account

These are conservative **proposed configuration limits**, not a
recommendation that live trading is suitable.

  Control                                         Proposed initial limit
  ------------------------------- --------------------------------------
  Reference balance                                              USD 500
  Risk per trade                    0.5% of equity (USD 2.50 at USD 500)
  Daily loss stop                                 2% (USD 10 at USD 500)
  Weekly loss stop                                4% (USD 20 at USD 500)
  Concurrent positions                                                 1
  Stop-loss                                                    Mandatory
  Martingale                                                    Disabled
  Grid / loss recovery                                          Disabled
  Averaging down                                                Disabled
  New trades after limit breach        Blocked until manual review/reset

Implementation requirements:

-   Calculate risk using actual broker contract specifications and
    account currency.
-   Include spread, commission, slippage, and swap assumptions.
-   If the minimum permitted position size risks more than the
    configured limit, reject the trade.
-   Apply daily and weekly limits to realized and unrealized loss as
    explicitly defined in configuration.
-   Include a maximum spread and maximum slippage threshold.
-   Never increase risk automatically after a losing trade.
-   Do not use leverage to force a position that does not fit the risk
    budget.

A USD 500 account may be too small for some instruments or brokers'
minimum trade sizes. If the broker cannot support the risk policy,
remain in demo mode or do not trade.

## 8. Security and operational controls

-   Store broker credentials and API keys in environment variables or a
    secrets manager; never commit them to source control.
-   Use a dedicated trading account with no withdrawal capability for
    the automation service, where the broker supports this.
-   Use least-privilege service accounts and restrict network access.
-   Encrypt communications between Ubuntu and Windows.
-   Enable operating-system updates, firewall rules, backups, and
    monitoring.
-   Keep an immutable or append-only audit trail for trade decisions and
    execution events.
-   Require manual confirmation for configuration changes that increase
    risk.
-   Provide a kill switch that blocks new orders and a separate
    procedure to manage existing open positions.
-   Define behavior for internet outages, terminal restarts, broker
    rejection, duplicate messages, and database unavailability.
-   Never store card details or withdrawal credentials in the
    application.

## 9. Development roadmap

  ------------------------------------------------------------------------
  Phase                 Deliverables                     Indicative effort
  --------------------- --------------------- ----------------------------
  1\. Platform and      Broker shortlist,                        2--3 days
  broker assessment     demo account, MT5     
                        connectivity proof    

  2\. Data foundation   Quote/candle                             3--5 days
                        ingestion,            
                        validation,           
                        PostgreSQL schema     

  3\. Strategy baseline One documented                           4--7 days
                        strategy, indicators, 
                        signal logs           

  4\. Risk and          Position sizing,                         4--6 days
  execution             limits, order         
                        validation, kill      
                        switch                

  5\. Dashboard and     Account view,                            5--7 days
  alerts                signals, trades,      
                        logs, notifications   

  6\. Backtesting and   Historical tests,              At least 2--4 weeks
  demo                  out-of-sample tests,  
                        demo execution        

  7\. Controlled live   Only after all                       No fixed date
  evaluation            acceptance gates pass 
  ------------------------------------------------------------------------

Indicative MVP engineering effort is approximately 6--10 weeks,
including integration and testing. The demo observation period may
extend this schedule. Do not use the calendar estimate as permission to
go live.

## 10. Estimated costs

Planning estimates only; verify actual provider prices before purchase.

### One-time / initial software costs

  Item                                                                            Estimate
  --------------------------------------- ------------------------------------------------
  MT5 terminal                              Usually no software charge; broker terms apply
  Python / FastAPI / PostgreSQL / React                                        Open source
  Telegram bot                                                 No charge for basic bot use
  AI API experiments                                           USD 5--15 initial allowance
  Domain (optional)                                      Approximately USD 10--15 per year

### Estimated monthly operating costs

  -----------------------------------------------------------------------
  Item                                                           Estimate
  ------------------------------ ----------------------------------------
  Windows VPS for MT5                                          USD 10--25

  Existing Ubuntu VPS                    USD 0 additional if already paid

  AI API usage                                                  USD 5--20

  Database / Telegram             USD 0 additional if self-hosted / basic
                                                                      use

  Broker trading costs           Variable; spreads, commission, swap, and
                                                                 slippage
  -----------------------------------------------------------------------

Indicative additional monthly infrastructure/API cost: **USD 15--45**,
assuming the existing Ubuntu VPS is suitable and already paid for. This
excludes development labor, broker deposits, trading losses, and any
paid market-data subscription.

## 11. One-time implementation checklist

Complete these steps in order. This is a finite project checklist, not
an instruction to run an unattended recurring task.

### Step 1 --- Confirm environment

-   [ ] Record Ubuntu VPS CPU, RAM, disk, public/private network setup,
    and firewall state.
-   [ ] Decide whether to provision a Windows VPS or use an existing
    Windows machine.
-   [ ] Confirm secure connectivity between the two hosts.

### Step 2 --- Select broker and demo account

-   [ ] Verify regulatory status and Saudi-resident eligibility.
-   [ ] Compare account type, instruments, minimum lot size, spreads,
    commission, swap, leverage, and execution conditions.
-   [ ] Open a demo account first.
-   [ ] Confirm MT5 login and market-data access.

### Step 3 --- Build the data pipeline

-   [ ] Connect to MT5 demo terminal.
-   [ ] Retrieve EUR/USD, GBP/USD, and USD/JPY data.
-   [ ] Validate timestamps, bid/ask, candle completeness, and stale
    data.
-   [ ] Store data and collection logs in PostgreSQL.

### Step 4 --- Implement and test one strategy

-   [ ] Write exact entry, exit, and no-trade rules.
-   [ ] Calculate indicators.
-   [ ] Backtest with realistic spread, commission, swap, and slippage.
-   [ ] Use out-of-sample and walk-forward evaluation.
-   [ ] Record drawdown, expectancy, trade count, profit factor, and
    performance by pair/timeframe.
-   [ ] Reject the strategy if results are unstable or not robust after
    costs.

### Step 5 --- Implement risk and execution controls

-   [ ] Implement position sizing from stop distance and contract
    specifications.
-   [ ] Enforce all configured loss and exposure limits.
-   [ ] Require stop-loss on every position.
-   [ ] Add duplicate-order protection and broker reconciliation.
-   [ ] Add a kill switch and fail-closed behavior.
-   [ ] Test broker rejections, disconnects, restarts, and partial
    fills.

### Step 6 --- Build dashboard and audit trail

-   [ ] Show account equity, balance, margin, open positions, and
    realized/unrealized P&L.
-   [ ] Show signals, reasons, rejected trades, and risk checks.
-   [ ] Show order/fill history and fees.
-   [ ] Add optional Telegram alerts.
-   [ ] Confirm that dashboard values reconcile with the broker.

### Step 7 --- Demo acceptance period

-   [ ] Run the system in demo mode with automated execution.
-   [ ] Observe it across different market conditions for at least 2--4
    weeks; longer is preferable.
-   [ ] Compare expected and actual fills, costs, and slippage.
-   [ ] Verify no risk-limit breaches, duplicate orders, or unexplained
    positions.
-   [ ] Review performance after all costs.
-   [ ] Fix and retest every material defect.

### Step 8 --- Live-trading gate

Do not enable live trading unless all of the following are true:

-   [ ] Broker/legal eligibility checks are complete.
-   [ ] Strategy has documented, reproducible out-of-sample results.
-   [ ] Demo results and execution reconciliation are satisfactory.
-   [ ] Risk engine and kill switch have passed failure testing.
-   [ ] Credentials and network access are secured.
-   [ ] User has reviewed and explicitly approved the live
    configuration.

If any gate fails, remain in demo mode. If live trading is later
approved, start with the smallest position size that satisfies the risk
policy and monitor closely. The system must be able to stop opening new
trades automatically when a limit or safety condition is reached.

## 12. MVP acceptance criteria

The first version is considered technically complete only when:

1.  It retrieves and validates demo market data.
2.  It produces repeatable signals from a versioned strategy.
3.  It records every signal and its inputs.
4.  The risk engine rejects invalid or oversized trades.
5.  The execution adapter can place, verify, and reconcile demo orders.
6.  Protective stops are verified at the broker.
7.  Disconnects and errors fail safely.
8.  The dashboard reconciles with the broker account.
9.  The kill switch blocks new orders.
10. Backtesting and demo results are documented with costs and drawdown.

Technical completion does not establish profitability or justify live
deployment.

## 13. Items to confirm before implementation

-   Current Ubuntu VPS specifications and provider.
-   Availability and cost of a Windows VPS.
-   Broker choice and confirmation of Saudi-resident eligibility.
-   Whether the first release should use MT5 only.
-   Preferred notification channel (Telegram or email).
-   Whether the dashboard is private to one user or needs multiple
    users.

------------------------------------------------------------------------

**Final recommendation:** Build a single-strategy, MT5-connected demo
system first. Keep the quantitative strategy, risk engine, and execution
service deterministic and auditable. Add LLM-based research and
reporting as optional supporting capabilities. Do not enable live
automated trading until the documented testing and safety gates have
passed.
