import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { AssessmentPanel } from "./Assessment.jsx";
import { InsightPanel } from "./Insight.jsx";
import { Badge, Card, Field, Modal, Switch, Table, countdown, digitsFor, fmt, modeLabel, statusTone, useAction, useApi, useToast } from "../components.jsx";

const STRAT = { trend_pullback: "EMA trend pullback", smc_sweep_bos: "SMC sweep → BOS → retest", manual: "manual" };
const TONE = { WAITING: "warn", APPROVED: "ok", DECLINED: "muted", EXPIRED: "muted", FAILED: "bad" };
const trendTone = (t) => (t === "up" || t === "bullish" ? "ok" : t === "down" || t === "bearish" ? "bad" : "muted");

export function SetupReport({ id, onClose, onDecided }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data: r, error } = useApi(`/setups/${id}/report`, { deps: [id], interval: 15000 });
  const [note, setNote] = useState("");
  const [run, busy] = useAction();
  if (error) return <Modal title="Setup review" onClose={onClose}><div className="alert bad">{error}</div></Modal>;
  if (!r) return <Modal title="Setup review" onClose={onClose}><p className="muted">Building the report…</p></Modal>;
  const s = r.signal, o = r.observed, d = digitsFor(s.symbol), rk = r.risk_now || {};
  const waiting = r.approval?.status === "WAITING";
  const decide = (what) => run(() => api.post(`/setups/${id}/${what}`, { note }))
    .then((x) => { toast(what === "approve" ? `Approved — order ${x.order_status} (${x.volume} lots)` : "Setup declined"); onDecided && onDecided(); onClose(); })
    .catch(() => {});
  const aiQ = `Review this ${s.side} setup on ${s.symbol} ${s.timeframe} (${STRAT[s.strategy] || s.strategy}). Hypothesis: ${r.hypothesis} Reasons for: ${r.reasons_for.join("; ")}. Reasons against: ${r.reasons_against.join("; ") || "none listed"}. Timeframe trends: ${Object.entries(r.timeframes).map(([k, v]) => `${k}=${v?.trend}/${v?.structure}`).join(", ")}. Risk now: lose ${rk.loss_if_sl} if SL, win ${rk.gain_if_tp} if TP. Answer with OBSERVED / INTERPRETATION / UNCERTAINTY.`;
  return (
    <Modal title={`${s.side} ${s.symbol} · ${s.timeframe} · ${STRAT[s.strategy] || s.strategy}`} onClose={onClose} wide>
      <div className="row gap wrap">
        <Badge tone={statusTone(s.side)}>{s.side}</Badge>
        {r.approval && <Badge tone={TONE[r.approval.status]}>{r.approval.status}</Badge>}
        {waiting && <span className="small muted">decide {countdown(r.approval.expires_at)} (expires)</span>}
        <span className="small muted">signal candle {fmt.dt(s.candle_time)} · v{s.version}</span>
      </div>
      <div className="alert info small" style={{ marginTop: 8 }}><b>Hypothesis.</b> {r.hypothesis}<br /><b>Invalidation.</b> {r.invalidation}</div>
      <details open={waiting} style={{ margin: "10px 0" }}><summary className="small"><b>Score, verdict and money at stake (net of costs)</b></summary>
        <AssessmentPanel signalId={id} live={waiting} />
      </details>

      <details open={waiting} style={{ margin: "10px 0" }}><summary className="small"><b>Time Machine odds and AI bull/bear review</b></summary>
        <InsightPanel signalId={id} />
      </details>

      <div className="outcome">
        <div><div className="lbl">Entry (signal)</div><div className="val">{fmt.px(o.entry_ref, d)}</div><div className="small muted">now {fmt.px(rk.entry_now, d)}</div></div>
        <div><div className="lbl">Stop-loss</div><div className="val neg">{fmt.px(o.stop_loss, d)}</div><div className="small muted">{o.stop_pips} pips</div></div>
        <div><div className="lbl">Take-profit</div><div className="val pos">{fmt.px(o.take_profit, d)}</div><div className="small muted">{r.targets.map((t) => `${t.r}R ${t.price}`).join(" · ")}</div></div>
        <div><div className="lbl">If stop hits</div><div className="val neg">−{fmt.money(rk.loss_if_sl)}</div><div className="small muted">{rk.volume} lots · limit {fmt.money(rk.risk_budget)}</div></div>
        <div><div className="lbl">If target hits</div><div className="val pos">+{fmt.money(rk.gain_if_tp)}</div><div className="small muted">R:R {rk.reward_risk ?? "—"}</div></div>
      </div>
      <div className={`alert ${rk.approved ? "ok" : "bad"} small`}>{rk.approved ? "✓ Risk engine would accept this trade right now." : `✕ Risk engine would block it now${rk.error ? `: ${rk.error}` : ""}.`}
        {rk.failed?.length > 0 && <ul className="checks">{rk.failed.map((c, i) => <li key={i} className="bad"><span>✕</span> {c.check} <span className="muted">{c.detail}</span></li>)}</ul>}
      </div>

      <div className="grid two">
        <Card title="Reasons FOR (observed)">
          <ul className="small">{r.reasons_for.length ? r.reasons_for.map((x, i) => <li key={i} className="pos">✓ {x}</li>) : <li className="muted">—</li>}</ul>
        </Card>
        <Card title="Reasons AGAINST / risks">
          <ul className="small">{r.reasons_against.length ? r.reasons_against.map((x, i) => <li key={i} className="neg">✕ {x}</li>) : <li className="muted">None found by the app's checks — that does not mean there are none.</li>}</ul>
        </Card>
      </div>

      <Card title="Market context (top-down)">
        <Table rows={Object.entries(r.timeframes).filter(([, v]) => v).map(([k, v]) => ({ id: k, ...v }))} cols={[
          { label: "TF", k: "tf" }, { label: "Trend (EMA)", render: (x) => <Badge tone={trendTone(x.trend)}>{x.trend}</Badge> },
          { label: "Structure (SMC)", render: (x) => <Badge tone={trendTone(x.structure)}>{x.structure}</Badge> },
          { label: "RSI", num: true, render: (x) => fmt.num(x.rsi, 1) }, { label: "ATR %", num: true, render: (x) => fmt.num(x.atr_pct, 3) },
          { label: "BSL above", render: (x) => <span className="mono small">{x.bsl.map((v) => v.toFixed(d)).join(" ") || "—"}</span> },
          { label: "SSL below", render: (x) => <span className="mono small">{x.ssl.map((v) => v.toFixed(d)).join(" ") || "—"}</span> },
        ]} />
        <p className="small muted">Session: {r.session.liquidity} · open: {r.session.sessions.filter((x) => x.open).map((x) => x.name).join(", ") || "none"}</p>
        {r.news.length > 0 && <ul className="feed small">{r.news.map((n, i) => <li key={i}><Badge tone={n.impact === "High" ? "bad" : "warn"}>{n.impact}</Badge><b>{n.currency}</b><span>{n.title}</span><span className="muted">in {n.minutes} min</span></li>)}</ul>}
        {r.smc_recent.length > 0 && (
          <p className="small"><b>Recent SMC events:</b> {r.smc_recent.slice(-6).map((e) => `${e.kind.toUpperCase()} ${e.dir} @ ${e.level ? Number(e.level).toFixed(d) : e.zone?.map((z) => Number(z).toFixed(d)).join("–")}`).join(" · ")}</p>
        )}
      </Card>
      <p className="small muted"><b>Uncertainty.</b> {r.uncertainty}</p>

      {r.approval && !waiting && <p className="small">Decision: <b>{r.approval.status}</b> by {r.approval.decided_by || "system"} {fmt.dt(r.approval.decided_at)} {r.approval.note && `— “${r.approval.note}”`}</p>}
      <div className="row end gap wrap">
        <a className="btn ghost sm" href={`#/assistant?q=${encodeURIComponent(aiQ)}`}>✦ Ask AI to review</a>
        {waiting && can("trading.manual") && (
          <>
            <input style={{ minWidth: 260 }} placeholder="Your reason (saved in the journal)" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn ghost" disabled={busy} onClick={() => decide("decline")}>Decline</button>
            <button className={`btn ${s.side === "BUY" ? "buy" : "sell"}`} disabled={busy || !rk.approved} onClick={() => decide("approve")}>Approve & place {s.side}</button>
          </>
        )}
      </div>
    </Modal>
  );
}

export default function Setups() {
  const { can } = useAuth();
  const { data, reload } = useApi("/setups?status=WAITING", { interval: 10000 });
  const { data: hist } = useApi("/setups?status=APPROVED,DECLINED,EXPIRED,FAILED&limit=50", { interval: 30000 });
  const { data: st } = useApi("/system/status", { interval: 15000 });
  const [open, setOpen] = useState(null);
  const [run] = useAction();
  const ctl = data?.controls || {};
  const cols = [
    { label: "Found", render: (x) => fmt.dt(x.candle_time) },
    { label: "Setup", render: (x) => <span><Badge tone={statusTone(x.side)}>{x.side}</Badge> {x.symbol} {x.timeframe}</span> },
    { label: "Strategy", render: (x) => STRAT[x.strategy] || x.strategy },
    { label: "Entry / SL / TP", render: (x) => <span className="mono small">{fmt.px(x.entry_ref, digitsFor(x.symbol))} / {fmt.px(x.stop_loss, digitsFor(x.symbol))} / {fmt.px(x.take_profit, digitsFor(x.symbol))}</span> },
  ];
  return (
    <div className="stack">
      <Card title="Approval rule" actions={st && <Badge tone="info">Mode: {modeLabel(st.trading.mode)}</Badge>}>
        <div className="row gap wrap">
          <span className="small">Auto-trade (no approval) is set per account in <a href="#/auto">Auto-trade</a>; everything else waits here.</span>
          <span className="small muted">A setup waits
            <select disabled={!can("trading.control")} value={ctl.approval_minutes || 60} onChange={(e) => run(() => api.put("/live/controls", { approval_minutes: Number(e.target.value) }), "Saved").then(reload)}>
              {[15, 30, 60, 120, 240].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select> for your decision, then expires.</span>
        </div>
        <p className="small muted">The strategy finds setups; the risk engine checks them; <b>you</b> decide. On approval the app re-checks price and risk at that moment — if the price already passed the stop or target, or a risk rule fails, the order is not placed.</p>
      </Card>
      <Card title={`Waiting for your decision (${data?.setups.length ?? 0})`}>
        <Table rows={(data?.setups || []).map((x) => ({ ...x, id: x.signal_id }))} onRow={(x) => setOpen(x.signal_id)} empty="No setups waiting. They appear here when the strategy finds one (on closed candles)." cols={[
          ...cols,
          { label: "Expires", render: (x) => countdown(x.expires_at) },
          { label: "", render: () => <button className="btn sm primary">Review</button> },
        ]} />
      </Card>
      <Card title="Decision history">
        <Table rows={(hist?.setups || []).map((x) => ({ ...x, id: x.signal_id }))} onRow={(x) => setOpen(x.signal_id)} cols={[
          ...cols,
          { label: "Decision", render: (x) => <Badge tone={TONE[x.status]}>{x.status}</Badge> },
          { label: "By", render: (x) => `${x.decided_by || "—"} ${x.decided_at ? fmt.dt(x.decided_at) : ""}` },
          { label: "Note", render: (x) => <span className="small muted trunc">{x.note}</span> },
        ]} />
      </Card>
      {open && <SetupReport id={open} onClose={() => setOpen(null)} onDecided={reload} />}
    </div>
  );
}
