import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, PasswordPrompt, Switch, fmt, useAction, useApi } from "../components.jsx";
import { CompareCard, HoldBadge, InsightSettingsCard } from "./Insight.jsx";

const MODE_INFO = {
  PAPER: ["Practice (forex paper)", "Fake money inside the app."],
  MT5_DEMO: ["Broker demo (Capital.com / cTrader / MT5)", "Your forex broker's demo account — fake money, real broker."],
  MT5_LIVE: ["Broker LIVE (forex)", "REAL MONEY. Needs the approved live gate + your password."],
  CRYPTO_PAPER: ["Crypto practice", "Fake USDT inside the app, real exchange prices."],
  CRYPTO_TEST: ["Crypto testnet", "Exchange test account — fake money, real exchange."],
  CRYPTO_LIVE: ["Crypto LIVE", "REAL MONEY. Needs the approved live gate + your password."],
};

function NewsAutomation() {
  const { can } = useAuth();
  const { data, reload } = useApi("/news-auto", { interval: 120000 });
  const [run, busy] = useAction();
  if (!data) return null;
  const edit = can("trading.control");
  const put = (b, m) => run(() => api.put("/news-auto", b), m).then(reload).catch(() => {});
  return (
    <div className="news-auto">
      <h4>News automation</h4>
      <div className="grid two">
        <div className="helper-box">
          <Switch on={data.breakout} disabled={!edit || busy} label="Trade the reaction after big news (news breakout)"
            onChange={(v) => put({ breakout: v }, v ? "News breakout ON" : "News breakout OFF")} />
          <div className="tiny muted">After a HIGH-impact release (CPI, jobs, central banks) or headline for a market, the app waits until the first spike is over,
            marks the spike's high and low, and only trades a clean candle close beyond it within {data.window_min} min. Stop at the other side, target 1.5R.
            It never guesses the news result. Placed automatically only where auto-trade is on (above); the risk engine checks every order.</div>
        </div>
        <div className="helper-box">
          <Switch on={data.ai_filter} disabled={!edit || busy || !data.ai_ready} label="AI news filter (Claude reads headlines)"
            onChange={(v) => put({ ai_filter: v }, v ? "AI news filter ON" : "AI news filter OFF")} />
          <div className="tiny muted">Claude tags each high-impact headline about your markets as bullish / bearish per currency or coin. A setup that strong recent news
            contradicts is rejected; agreeing news is noted. The AI can only block a trade — it never places one.{!data.ai_ready && " Turn on the AI assistant first."}</div>
          {data.tags.length > 0 && <details className="small"><summary>Latest AI tags ({data.tags.length})</summary>
            {data.tags.map((t, i) => <div key={i} className="tiny" style={{ margin: "4px 0" }}>
              {Object.entries(t.effects).map(([k, v]) => <span key={k} className={v === "bullish" ? "pos" : v === "bearish" ? "neg" : "muted"}>{k} {v} </span>)}
              <span className="muted">· {Math.round(t.confidence * 100)}% · {t.title}</span></div>)}
          </details>}
        </div>
      </div>
    </div>
  );
}

export function AutomationCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/automation");
  const [run, busy] = useAction();
  const [ask, setAsk] = useState(null);
  if (!data) return null;
  const editable = can("trading.control");
  const put = (body, msg) => run(() => api.put("/automation", body), msg).then(reload);
  const flip = (m, on) => {
    if (on && m.live) setAsk(m.mode);
    else put({ mode: m.mode, enabled: on }, on ? `Auto-trade ON for ${MODE_INFO[m.mode][0]}` : `Auto-trade OFF for ${MODE_INFO[m.mode][0]}`).catch(() => {});
  };
  return (
    <Card title="Auto-trade (place qualified trades without asking me)">
      <p className="small muted">
        Auto-trade works on <b>strategy signals</b> (and on <b>news breakouts</b> if switched on below). When a setup reaches <b>QUALIFIED</b>, has score ≥ <b>{data.min_score}</b> and verdict ≥ <b>{data.min_verdict.replace("_", " ")}</b>,
        the app places it by itself in the switched-on accounts and sends you an <b>AUTO TRADE</b> alert (Telegram + email).
        The risk engine still checks every order: size, daily loss limit, max trades, news filter, kill switch.
        Switched off = the setup waits for you on <a href="#/setups">Setups to approve</a>.
      </p>
      <div className="auto-grid">
        {data.modes.map((m) => (
          <div key={m.mode} className={`auto-mode ${m.live ? "live" : ""}`}>
            <Switch on={m.enabled} disabled={!editable || busy || (m.live && !data.live_gate_approved && !m.enabled)}
              onChange={(on) => flip(m, on)} label={MODE_INFO[m.mode][0]} />
            <div className="tiny muted">{MODE_INFO[m.mode][1]}</div>
            {m.enabled && !m.effective && <div className="tiny warn">Switched on but not active (live gate not approved or expired).</div>}
            {m.effective && <Badge tone={m.live ? "bad" : "good"}>active</Badge>}
          </div>
        ))}
      </div>
      {!data.live_gate_approved && <p className="tiny muted">Real-money switches are locked until the live gate is approved (Risk &amp; Strategy page).</p>}
      {editable && (
        <div className="row gap wrap" style={{ marginTop: 8 }}>
          <Field label="Minimum score (50–100)">
            <select value={data.min_score} onChange={(e) => put({ min_score: Number(e.target.value) }, "Saved").catch(() => {})}>
              {[60, 65, 70, 75, 80, 85, 90].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </Field>
          <Field label="Minimum verdict">
            <select value={data.min_verdict} onChange={(e) => put({ min_verdict: e.target.value }, "Saved").catch(() => {})}>
              <option value="PAPER_TEST">PAPER TEST (more trades)</option>
              <option value="RESEARCH_SUPPORTED">RESEARCH SUPPORTED (fewer, stricter)</option>
            </select>
          </Field>
        </div>
      )}
      <NewsAutomation />
      {ask && (
        <PasswordPrompt title="Turn on REAL-MONEY auto-trading?" onClose={() => setAsk(null)}
          message="The app will place real orders without asking you, within your risk limits. You can switch it off any time; the kill switch stops everything."
          onSubmit={(pw) => put({ mode: ask, enabled: true, password: pw }, "Real-money auto-trade ON")} />
      )}
    </Card>
  );
}

function Catalog({ data, onApply, canEdit }) {
  return (
    <Card title="Strategy library — what each one does">
      <p className="small muted">{data.honesty}</p>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Strategy</th><th>Style</th><th>Typical win rate</th><th>Payoff</th><th>Rules</th><th>Use for</th></tr></thead>
          <tbody>{data.catalog.map((s) => (
            <tr key={s.name}>
              <td><b className="small">{s.label || s.name}</b>
                <div className="tiny muted">{s.name}
                  {data.live.forex === s.name && <> · <Badge tone="info">forex now</Badge></>}
                  {data.live.crypto === s.name && <> · <Badge tone="info">crypto now</Badge></>}
                </div></td>
              <td className="small">{s.family}</td>
              <td className="small">{s.typical_win_rate}</td>
              <td className="small">{s.payoff}</td>
              <td className="tiny wrap">{s.rules}<div className="muted">Best: {s.best_when} · Weak: {s.weak_when}</div></td>
              <td>{canEdit && <div className="stack-xs">
                <button className="btn ghost sm" onClick={() => onApply("crypto", s.name)}>Crypto</button>
                <button className="btn ghost sm" onClick={() => onApply("forex", s.name)}>Forex</button>
              </div>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function Spark({ data }) {
  if (!data || data.length < 2) return <span className="tiny muted">—</span>;
  const W = 110, H = 28, lo = Math.min(0, ...data), hi = Math.max(0, ...data);
  const X = (i) => (i / (data.length - 1)) * W, Y = (v) => H - 2 - ((v - lo) / (hi - lo || 1)) * (H - 4);
  return (
    <svg width={W} height={H}><line x1="0" x2={W} y1={Y(0)} y2={Y(0)} stroke="var(--line2)" />
      <path d={data.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("")} fill="none" stroke={data[data.length - 1] >= 0 ? "var(--ok)" : "var(--bad)"} strokeWidth="1.5" /></svg>
  );
}

function RaceCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/race", { interval: 60000 });
  const [run, busy] = useAction();
  const [tfs, setTfs] = useState(null);
  if (!data) return null;
  const cur = tfs || data.config.timeframes || ["H1"];
  return (
    <Card title="🏁 Strategy race — every strategy paper-trades side by side" actions={can("strategy.edit") && (
      <div className="row gap wrap">
        {["M15", "H1", "H4"].map((t) => <label key={t} className="check small"><input type="checkbox" checked={cur.includes(t)} onChange={() => setTfs(cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t])} /> {t}</label>)}
        <button className="btn sm" disabled={busy || !cur.length} onClick={() => { if (window.confirm("Start a new race? Current race results are cleared.")) run(() => api.post("/race/reset", { timeframes: cur }), "New race started").then(reload); }}>Restart race</button>
      </div>
    )}>
      <p className="small muted">On every closed {(data.config.timeframes || []).join(" / ")} candle, all strategies look at all your markets at the same time. Each signal becomes a virtual
        1R trade (spread and fees included). Results are in <b>R</b> so markets compare fairly. Started {fmt.dt(data.config.started_at)}. Trust a strategy only after 30+ trades.</p>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>#</th><th>Strategy</th><th>Trades</th><th>Open</th><th>Win %</th><th>Total R</th><th>Avg R</th><th>Profit factor</th><th>Max DD (R)</th><th>Equity in R</th></tr></thead>
          <tbody>{data.board.map((b, i) => (
            <tr key={b.strategy}>
              <td>{i + 1}</td><td><b className="small">{b.strategy}</b>{!b.reliable && b.trades > 0 && <div className="tiny muted">too few trades</div>}</td>
              <td>{b.trades}</td><td>{b.open}</td><td>{b.win_rate_pct ?? "—"}</td>
              <td className={b.total_r > 0 ? "pos" : b.total_r < 0 ? "neg" : ""}>{b.total_r > 0 ? "+" : ""}{b.total_r}</td>
              <td>{b.avg_r ?? "—"}</td><td>{b.profit_factor ?? "—"}</td><td>{b.max_dd_r}</td><td><Spark data={b.curve} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {data.recent.length > 0 && <details className="small" style={{ marginTop: 8 }}><summary>Latest race trades ({data.recent.length})</summary>
        <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Time</th><th>Strategy</th><th>Market</th><th>Side</th><th>Status</th><th>R</th></tr></thead>
          <tbody>{data.recent.map((t) => <tr key={t.id}><td>{fmt.dt(t.time)}</td><td>{t.strategy}</td><td>{t.symbol} {t.tf}</td><td className={t.side === "BUY" ? "pos" : "neg"}>{t.side}</td>
            <td>{t.status}</td><td className={t.r > 0 ? "pos" : t.r < 0 ? "neg" : ""}>{t.r ?? "open"}</td></tr>)}</tbody></table></div></details>}
    </Card>
  );
}

export default function StrategyLab() {
  const { can } = useAuth();
  const { data, reload } = useApi("/lab", { interval: 5000 });
  const [run, busy] = useAction();
  const [tfs, setTfs] = useState(["H1", "H4"]);
  const [days, setDays] = useState(180);
  const [review, setReview] = useState("");
  if (!data) return <p className="muted">Loading…</p>;
  const apply = (target, strategy) => run(() => api.post("/lab/apply", { target, strategy }), `${strategy} is now the ${target} strategy`).then(reload).catch(() => {});
  const running = data.running || data.status === "RUNNING";
  return (
    <div className="stack">
      <AutomationCard />
      <InsightSettingsCard />
      <RaceCard />
      <Catalog data={data} onApply={apply} canEdit={can("strategy.edit")} />
      {can("backtest.run") && <CompareCard catalog={data.catalog} markets={data.markets || []} />}
      <Card title="Test every strategy on my markets (with costs)" actions={
        <button className="btn primary sm" disabled={busy || running}
          onClick={() => run(() => api.post("/lab/run", { timeframes: tfs, backfill_days: Number(days) }), "Strategy lab started").then(reload).catch(() => {})}>
          {running ? `Running… ${data.progress || ""}` : "Run strategy lab"}
        </button>
      }>
        <p className="small muted">
          Runs every strategy on every forex and crypto market you watch, including spread, fees and slippage.
          The last 20% of the data is kept apart as a <b>final test</b> — that's the column to trust. For crypto it can first download
          older candles from your exchange (public data, no keys needed).
        </p>
        <div className="row gap wrap">
          <div className="field"><span className="field-l">Timeframes</span>
            <div className="row gap">{["M15", "H1", "H4", "D1"].map((t) => (
              <label key={t} className="check small"><input type="checkbox" checked={tfs.includes(t)}
                onChange={() => setTfs(tfs.includes(t) ? tfs.filter((x) => x !== t) : [...tfs, t])} /> {t}</label>
            ))}</div>
          </div>
          <Field label="Download crypto history (days)">
            <select value={days} onChange={(e) => setDays(e.target.value)}>
              {[0, 90, 180, 365, 730].map((d) => <option key={d} value={d}>{d === 0 ? "No — use what I have" : `${d} days`}</option>)}
            </select>
          </Field>
        </div>
        {data.status === "FAILED" && <div className="alert bad small">Failed: {String(data.error).split("\n")[0]}</div>}
        {data.finished_at && <p className="tiny muted">Last run: {fmt.dt(data.finished_at)} · {data.results.length} results · {data.skipped?.length || 0} skipped (not enough candles)</p>}
      </Card>
      {data.results?.length > 0 && (
        <Card title="Ranking (best first)" actions={can("ai.use") && (
          <button className="btn sm" disabled={busy} onClick={() => run(() => api.post("/lab/ai-review")).then((r) => setReview(r.review)).catch(() => {})}>Ask AI to review</button>
        )}>
          {review && <pre className="alert-body">{review}</pre>}
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Market</th><th>TF</th><th>Strategy</th><th>Trades</th><th>Win %</th><th>Profit factor</th><th>Avg R</th>
                <th>Max DD %</th><th>Final test (trades · win % · PF · avg R)</th><th>vs hold</th><th>Verdict</th><th></th></tr></thead>
              <tbody>{data.results.slice(0, 60).map((r, i) => (
                <tr key={i}>
                  <td>{r.symbol}</td><td>{r.tf}</td><td className="small">{r.strategy}</td><td>{r.trades}</td>
                  <td>{fmt.num(r.win_rate_pct, 1)}</td><td>{fmt.num(r.profit_factor, 2)}</td><td>{fmt.num(r.avg_r, 2)}</td><td>{fmt.num(r.max_dd_pct, 1)}</td>
                  <td className="small">{r.final_test.trades ?? 0} · {fmt.num(r.final_test.win_rate_pct, 0)}% · {fmt.num(r.final_test.profit_factor, 2)} · {fmt.num(r.final_test.avg_r, 2)}</td>
                  <td><HoldBadge v={r.vs_hold} /></td>
                  <td><Badge tone={r.promising ? "good" : r.verdict.startsWith("TOO") ? "muted" : "bad"}>{r.verdict}</Badge></td>
                  <td>{can("strategy.edit") && r.promising && <button className="btn ghost sm" onClick={() => apply(r.crypto ? "crypto" : "forex", r.strategy)}>Use</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="tiny muted">Past results don't guarantee future results. "Use" switches the strategy for paper/demo first — keep it there for at least 2–4 weeks before trusting it with real money.</p>
        </Card>
      )}
    </div>
  );
}
