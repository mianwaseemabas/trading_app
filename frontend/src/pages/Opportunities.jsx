import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Modal, Switch, fmt, useAction, useApi } from "../components.jsx";

const DTONE = { "TRADE CANDIDATE": "good", WATCH: "warn", REJECT: "bad", "NO TRADE": "muted" };
const ICON = { pass: "✓", warn: "!", fail: "✕" };
const digitsOf = (s, px) => (s.endsWith("JPY") ? 3 : px > 1000 ? 2 : px > 20 ? 3 : 5);
const STRAT = { trend_pullback: "EMA trend pullback", smc_sweep_bos: "SMC sweep → BOS", donchian_breakout: "Donchian breakout",
  rsi2_meanrev: "RSI-2 mean reversion", bb_squeeze: "Bollinger squeeze", ema_cross_adx: "EMA cross + ADX" };

function SignalCard({ x, onQueue, onExplain, canTrade, canAi, busy }) {
  const d = digitsOf(x.symbol, x.entry || 1);
  const e = x.economics || {};
  const label = x.decision === "TRADE CANDIDATE" ? `${x.side} candidate` : x.decision === "WATCH" ? `${x.side} · watch` : x.decision === "REJECT" ? "Rejected" : "No trade";
  const rr = e.net_rr ?? e.gross_rr;
  return (
    <div className={`sigcard d-${x.decision.replace(" ", "-").toLowerCase()}`}>
      <div className="row between">
        <div>
          <div className="tiny muted">Qivynex · AI Signal Intelligence · {STRAT[x.strategy] || x.strategy} · {x.tf}</div>
          <div className="sc-sym">{x.symbol.length === 6 && !x.symbol.endsWith("T") ? `${x.symbol.slice(0, 3)}/${x.symbol.slice(3)}` : x.symbol}</div>
        </div>
        <Badge tone={DTONE[x.decision]}>{label}</Badge>
      </div>
      <div className="row between" style={{ alignItems: "flex-end", marginTop: 6 }}>
        <div><div className="tiny muted">Signal quality</div><div className={`sc-score ${x.score >= 70 ? "pos" : x.score >= 55 ? "warnc" : "neg"}`}>{x.score}/100</div></div>
        <div style={{ textAlign: "right" }}><div className="tiny muted">Risk/reward {e.net_rr != null ? "(after costs)" : ""}</div><div className="sc-rr">{rr != null ? `1 : ${rr}` : "—"}</div></div>
      </div>
      <div className="sc-bar"><div style={{ width: `${x.score}%` }} className={x.score >= 70 ? "ok" : x.score >= 55 ? "warn" : "bad"} /></div>
      <div className="sc-levels">
        <div><span>Entry</span><b>{fmt.px(x.entry, d)}</b></div>
        <div><span>Stop loss</span><b>{fmt.px(x.stop, d)}</b></div>
        <div><span>Target</span><b>{fmt.px(x.target, d)}</b></div>
      </div>
      {e.available ? (
        <div className="sc-money">
          <div><span>Potential gain (after costs)</span><b className="pos">+{fmt.money(e.net_target)}</b><small>gross +{fmt.money(e.gross_target)}</small></div>
          <div><span>Planned loss (incl. costs)</span><b className="neg">−{fmt.money(e.net_stop)}</b><small>gross −{fmt.money(e.gross_stop)}</small></div>
        </div>
      ) : <div className="alert warn tiny">{e.reason || "Size not available for your account."}</div>}
      {e.available && <div className="tiny muted">Size {e.quantity} {e.unit} · spread {fmt.money(e.spread_cost_in_entry)} · commission {fmt.money(e.commission_round_trip)} · slippage allowance {fmt.money(e.slippage_allowance)}
        {e.break_even_win_rate_pct != null && <> · must win ≥ {e.break_even_win_rate_pct}% to break even</>}</div>}
      <div className="sc-ev-title">AI evidence</div>
      <ul className="sc-ev">{x.evidence.map((i, k) => <li key={k} className={i.ok}><span>{ICON[i.ok]}</span>{i.text}</li>)}</ul>
      <div className={`sc-hist h-${x.history.level}`}>📊 {x.history.lines.join(" · ")}</div>
      <div className="row between wrap gap sc-foot">
        <span className="tiny muted">Decision: {x.decision === "TRADE CANDIDATE" ? "passes all checks — awaits your rules (approval or auto-trade)" : x.decision === "WATCH" ? "evidence not strong enough yet" : x.decision === "REJECT" ? "a hard check failed (see ✕)" : "evidence too weak"}
          {x.fresh_bars > 0 && ` · signal ${x.fresh_bars} candle(s) ago`}</span>
        <span className={`route-tag r-${x.route.kind}`}>{x.route.short}</span>
      </div>
      <div className="row gap wrap">
        <a className="btn sm ghost" href={`#/terminal?s=${x.symbol}`}>Chart</a>
        {canAi && <button className="btn sm ghost" disabled={busy} onClick={() => onExplain(x)}>🤖 Explain evidence</button>}
        {canTrade && x.decision === "TRADE CANDIDATE" && <button className="btn sm primary" disabled={busy} onClick={() => onQueue(x)}>
          {x.route.auto ? "Send (auto-trade is ON → places it)" : "Send to approval"}</button>}
      </div>
    </div>
  );
}

export default function Opportunities() {
  const { can } = useAuth();
  const { data, reload } = useApi("/opportunities", { interval: 60000 });
  const [run, busy] = useAction();
  const [show, setShow] = useState("actionable");
  const [ai, setAi] = useState(null);
  const [res, setRes] = useState(null);
  if (!data) return <p className="muted">Loading…</p>;
  const items = (data.items || []).filter((x) => show === "all" || (show === "actionable" ? ["TRADE CANDIDATE", "WATCH"].includes(x.decision) : x.decision === "REJECT"));
  const c = data.counts || {};
  return (
    <div className="stack">
      <Card title="🎯 Opportunity ranking — which setups are actionable right now" actions={
        <button className="btn sm primary" disabled={busy} onClick={() => run(() => api.post("/opportunities/scan"), "Scanned").then(reload).catch(() => {})}>{busy ? "Scanning…" : "Scan now"}</button>
      }>
        <p className="small muted">Every 5 minutes all strategies are checked on all your markets (15m, 1H, 4H). A pattern alone is not enough: each setup is checked for
          data quality, trend/structure/momentum, spread and costs, stop distance, your risk budget and limits, and news — then ranked by quality, measured history and freshness. {data.note}</p>
        <div className="row gap wrap">
          <span className="chip on">✓ {c["TRADE CANDIDATE"] || 0} trade candidates</span><span className="chip">{c.WATCH || 0} watch</span>
          <span className="chip rej">{c.REJECT || 0} rejected</span><span className="chip">{c["NO TRADE"] || 0} no trade</span>
          <span className="tiny muted">{data.at ? `last scan ${fmt.dt(data.at)} · ${data.markets} markets · ${data.setups} fresh setups · ${data.seconds}s` : "not scanned yet — press Scan now"}</span>
        </div>
        {can("trading.control") && <div style={{ marginTop: 8 }}><Switch on={data.config?.auto_proven} disabled={busy}
          label="Auto-send the best candidate whose strategy has PROVEN results (race 30+ trades positive, or lab PROMISING)"
          onChange={(v) => run(() => api.put("/opportunities/config", { auto_proven: v }), v ? "On" : "Off").then(reload)} />
          <div className="tiny muted">Only on accounts where auto-trade is on; otherwise it goes to Setups to approve. The risk engine re-checks it at today's price.</div></div>}
      </Card>
      <div className="seg">{[["actionable", "Actionable"], ["rejected", "Rejected (why)"], ["all", "All"]].map(([k, l]) => <button key={k} className={show === k ? "active" : ""} onClick={() => setShow(k)}>{l}</button>)}</div>
      {items.length === 0 && <p className="muted">Nothing here right now. Doing nothing is a valid decision — the market will offer the next setup.</p>}
      <div className="sig-grid">
        {items.map((x) => <SignalCard key={x.key} x={x} busy={busy} canTrade={can("trading.manual")} canAi={can("ai.use")}
          onQueue={(o) => run(() => api.post(`/opportunities/${o.key}/queue`)).then((r) => { setRes(r); reload(); }).catch(() => {})}
          onExplain={(o) => { setAi({ loading: true, x: o }); run(() => api.post("/opportunities/explain", { key: o.key })).then((r) => setAi({ x: o, text: r.analysis })).catch(() => setAi(null)); }} />)}
      </div>
      {res && <Modal title={res.ok ? "Done" : "Not sent"} onClose={() => setRes(null)}><p>{res.detail}</p>{res.ok && <a className="btn sm" href="#/setups">Open Setups →</a>}</Modal>}
      {ai && <Modal title={`AI signal analyst · ${ai.x.symbol} ${ai.x.side} ${ai.x.tf}`} onClose={() => setAi(null)} wide>
        {ai.loading ? <p className="muted">Claude is reviewing the evidence…</p> : <div className="brief">{ai.text}</div>}
        <p className="tiny muted">Explains the evidence only — not advice, not a prediction. The score is not a win probability.</p></Modal>}
    </div>
  );
}
