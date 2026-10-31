import React from "react";
import { useAuth } from "../App.jsx";
import { Card, useApi } from "../components.jsx";

const GLOSSARY = [
  ["Pip / point", "The smallest price steps. On EURUSD 1 pip = 0.0001 = 10 points. On USDJPY 1 pip = 0.01."],
  ["Lot", "Trade size. 1 lot = 100,000 units of the first currency; 0.01 lot (a micro lot) = 1,000 units. On EURUSD 0.01 lot ≈ $0.10 per pip."],
  ["Stop-loss (SL)", "A price where the broker closes your trade automatically to limit the loss. Every trade in this app must have one."],
  ["Take-profit (TP)", "A price where the trade closes automatically in profit. The strategy places it at 2× the stop distance."],
  ["Spread", "The difference between the buy (ask) and sell (bid) price — a cost you pay on every trade."],
  ["Risk per trade", "How much of your account you can lose if the stop-loss is hit. 0.5% of $500 = $2.50. The app sizes each trade from this."],
  ["Equity vs balance", "Balance counts only closed trades. Equity = balance + profit/loss of open trades."],
  ["EMA 50 / 200", "Moving averages (yellow / purple on the chart). EMA 50 above EMA 200 = uptrend; below = downtrend."],
  ["RSI", "Momentum from 0–100. Above 70 = stretched up, below 30 = stretched down. The strategy buys only with RSI 50–70."],
  ["MACD histogram", "Shows momentum turning. The strategy waits for it to cross zero in the trend direction."],
  ["ATR", "Average True Range — how much the price typically moves per candle. Used to place the stop-loss (1.5 × ATR)."],
  ["Backtest", "Running the strategy on past prices to see how it would have done. Good past results do not guarantee future profit."],
  ["Out-of-sample", "The last part of the history that the strategy was not tuned on — the most honest test."],
  ["Profit factor", "Gross profit ÷ gross loss. Above 1 = profitable after costs; above ~1.3 over many trades is encouraging."],
  ["Drawdown", "The biggest fall from a peak in your equity. Shows how painful the losing streaks were."],
  ["Market / Limit / Stop order", "Market = now at the current price. Limit = wait for a better price (buy lower / sell higher). Stop = wait for a breakout (buy higher / sell lower)."],
  ["Risk $ (limit amount)", "In the order ticket choose 'Risk $' and type e.g. 2: the app sizes the trade so hitting the stop-loss loses about $2 (never more than your risk limit)."],
  ["R / R-multiple", "1R = the amount you risked. Closing at +2R means you won twice what you risked."],
  ["Break-even / trailing stop", "Auto-protect: move the stop to your entry once the trade is +1R in profit, or let it follow the price at a distance of N × ATR."],
  ["High-impact news", "Scheduled economic releases (rates, jobs, inflation) that can move prices sharply. The news filter blocks new trades around them."],
  ["Swing high / low", "A candle whose high (low) is beyond the 3 candles on each side. Only confirmed 3 candles later, so the app never uses future data."],
  ["BSL / SSL", "Buy-side liquidity above swing highs and sell-side liquidity below swing lows — places where stop orders may cluster."],
  ["Liquidity sweep", "Price wicks beyond a swing level and closes back inside. A chart cannot prove it was a deliberate 'stop hunt'."],
  ["BOS / CHoCH (MSS)", "A candle closes beyond the latest swing level. BOS = in the direction of the current structure; CHoCH = it flips the structure."],
  ["Displacement", "A strong candle: body ≥ 1.2 × ATR and at least half of its range."],
  ["Fair value gap (FVG)", "A three-candle gap: candle 3's low above candle 1's high (bullish) or the mirror. A revisit is possible, not guaranteed."],
  ["Order block (OB)", "The last opposite-colour candle before a structure break. A zone by definition — not proof of bank orders."],
  ["Expectancy", "Average profit per trade after costs. Must be positive over many trades; a high win rate alone means nothing."],
  ["Kill switch", "Emergency stop: blocks all new orders immediately (optionally closes open positions)."],
];

const MODES = [
  ["OFF", "Nothing happens."],
  ["SIGNALS ONLY", "The strategy analyses each new candle and logs BUY/SELL signals, but places no orders."],
  ["PRACTICE (PAPER)", "Paper trading = practice. A virtual $500 account inside this app, filled at real broker prices. Nothing goes to a broker and no money can be lost. Start here."],
  ["BROKER DEMO", "Real orders on your broker's demo (practice-money) account."],
  ["BROKER LIVE", "Real money. Locked until the live checklist is approved and real-money orders are allowed on the Brokers page."],
];

function Step({ done, title, how, link }) {
  return (
    <li className={done ? "ok" : ""}>
      <span className="tick">{done ? "✓" : "○"}</span>
      <div>
        <b>{title}</b>
        <div className="muted small">{how} {link && <a href={link}>Open →</a>}</div>
      </div>
    </li>
  );
}

export default function Guide() {
  const { user, can } = useAuth();
  const { data: st } = useApi(can("dashboard.view") ? "/system/status" : null);
  const { data: br } = useApi("/brokers");
  const { data: inst } = useApi(can("market.view") ? "/market/instruments" : null);
  const { data: bts } = useApi(can("backtest.run") ? "/backtests" : null);
  const { data: risk } = useApi("/system/risk");
  const { data: ai } = useApi(can("ai.use") ? "/ai/settings" : null);
  const active = br?.active;
  const brokerOk = active && active !== "none" && br.connectors[active]?.status === "connected";
  const candles = (inst?.history || []).reduce((n, h) => n + h.count, 0);
  const steps = [
    { done: !user.must_change_password && user.totp_enabled, title: "Secure your account", how: "Enable two-factor authentication on My account.", link: "#/account" },
    { done: brokerOk, title: "Connect your broker", how: "Brokers page → enter your Capital.com (or cTrader) demo credentials → Use this broker.", link: "#/brokers" },
    { done: st?.data?.source === "mt5" && candles > 1000, title: "Use real broker prices", how: "System → Use broker data. History downloads automatically.", link: "#/settings" },
    { done: risk?.risk?.commission_per_lot === 0 || active !== "capital", title: "Set costs for your broker", how: "Capital.com has no commission: Risk & Strategy → Commission per lot = 0.", link: "#/risk" },
    { done: (bts || []).some((b) => b.status === "DONE" && b.summary?.trades > 0), title: "Run a backtest", how: "Backtest → EURUSD, H1 → Run. Read the warnings and out-of-sample results.", link: "#/backtest" },
    { done: ["PAPER", "MT5_DEMO", "MT5_LIVE"].includes(st?.trading?.mode), title: "Start practice (paper) trading", how: "Dashboard → PRACTICE (PAPER). Then Live trading → + New order → Risk $ → Place. Watch it for a week.", link: "#/dashboard" },
    { done: st?.trading?.mode === "MT5_DEMO" || st?.trading?.mode === "MT5_LIVE", title: "Trade on the broker demo", how: "Dashboard → BROKER DEMO for 2–4 weeks; compare with your broker app.", link: "#/dashboard" },
    { done: !!ai?.enabled, title: "Turn on your AI coach (optional)", how: "AI Assistant → choose provider, model and API key.", link: "#/assistant" },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <div className="stack">
      <Card title={`Getting started — ${doneCount} of ${steps.length} done`}>
        <ul className="steps">{steps.map((s) => <Step key={s.title} {...s} />)}</ul>
      </Card>
      <div className="grid two">
        <Card title="Your research → approval workflow">
          <ol className="small">
            <li><b>Market overview</b>: sessions, H4 → H1 → M15 trend, volatility, key liquidity and upcoming news.</li>
            <li><b>SMC scanner</b>: swings, BSL/SSL, sweeps, BOS/CHoCH and FVGs drawn from the app's written rules.</li>
            <li><b>Setups to approve</b>: when the strategy finds a setup it waits for you, with a report: hypothesis, invalidation, targets, reasons for and against, risk in $. Approve or decline with a reason.</li>
            <li><b>Live trading</b>: watch the trade, move the stop, add a screenshot and a journal note.</li>
            <li><b>Trades → Performance</b>: expectancy, profit factor, drawdown, average R — by market, session and strategy.</li>
            <li><b>Backtest</b>: compare SMC vs the EMA baseline on 60% development / 20% validation / 20% final-test data.</li>
          </ol>
          <p className="small muted">Success at first = a repeatable process, not money. Stay on demo until 50+ trades and the final-test results are positive after costs. With a ~$100 account, minimum lot sizes may make your risk limit impossible — the app will refuse those trades rather than over-risk.</p>
        </Card>
        <Card title="How the app decides a trade">
          <ol className="small">
            <li>Every 15 minutes / hour a candle closes. The strategy checks trend (EMA), momentum (MACD, RSI) and volatility (ATR).</li>
            <li>If all conditions agree it creates a <b>BUY</b> or <b>SELL</b> signal with a stop-loss (1.5 × ATR) and take-profit (2 × stop).</li>
            <li>The <b>risk engine</b> checks everything: kill switch, loss limits, spread, data freshness, open positions, and sizes the trade so a stop-loss hit costs at most your risk per trade.</li>
            <li>If approved, the order goes to Paper or your broker; the stop-loss is verified at the broker.</li>
            <li>Everything is recorded: Signals (why), Trades (result), Audit (who did what).</li>
          </ol>
        </Card>
        <Card title="Place your first trade (practice)">
          <ol className="small">
            <li>Dashboard → click <b>PRACTICE (PAPER)</b>.</li>
            <li>Live trading → <b>+ New order</b>. Pick EURUSD and BUY or SELL.</li>
            <li>Keep the suggested stop-loss/take-profit. Under <b>Trade size</b> pick <b>Risk $</b> — it fills in your maximum (0.5% of the account = $2.50 on $500); type a smaller amount if you want.</li>
            <li>Read "If stop-loss hits −$… / If take-profit hits +$…" and press <b>Place</b>.</li>
            <li>Click the position to watch it on a chart, move the stop, and write a journal note. After it closes, see Trades → Performance.</li>
          </ol>
        </Card>
        <Card title="Trading modes">
          <dl className="dl">{MODES.map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}</dl>
        </Card>
        <Card title="Daily routine (5 minutes)">
          <ol className="small">
            <li><b>News</b>: any red (High) events today for your currencies? Avoid opening trades just before them.</li>
            <li><b>Live trading</b>: check open positions, P&L and that every position has a stop-loss.</li>
            <li><b>Dashboard</b>: broker connection green, no LOCKED / RECONCILIATION badges, loss meters well below limits.</li>
            <li><b>Signals</b>: read why trades were taken or rejected (click a row). Ask the AI coach to explain anything unclear.</li>
            <li>Weekly: <b>Trades → Performance</b> — net result after costs, win rate, drawdown.</li>
            <li>Never raise risk to win back losses. If something looks wrong, press the <b>kill switch</b> first, then investigate.</li>
          </ol>
        </Card>
        <Card title="Glossary">
          <dl className="dl">{GLOSSARY.map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}</dl>
        </Card>
      </div>
    </div>
  );
}
