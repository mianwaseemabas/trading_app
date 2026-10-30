import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Modal, Stat, Table, digitsFor, fmt, modeTone, pnlClass, statusTone, useAction, useApi, useToast } from "../components.jsx";

const ACTION_LABEL = {
  "order.created": "Order created", "order.filled": "Order filled", "order.rejected": "Order rejected", "position.closed": "Position closed",
  "order.manual": "Manual order", "trading.kill_switch": "Kill switch", "trading.mode_changed": "Mode changed", "risk.locked": "Loss limit lock",
};

function Sparkline({ points }) {
  if (points.length < 2) return <div className="muted small">Collecting live equity…</div>;
  const w = 260, h = 46;
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const X = (i) => (i / (points.length - 1)) * w;
  const Y = (v) => h - 4 - ((v - lo) / (hi - lo || 1)) * (h - 8);
  const up = ys[ys.length - 1] >= ys[0];
  return (
    <svg width={w} height={h} className="spark">
      <path d={points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join("")} fill="none" stroke={up ? "var(--ok)" : "var(--bad)"} strokeWidth="1.8" />
    </svg>
  );
}

function OrderTicket({ symbols, onClose, onPlaced }) {
  const toast = useToast();
  const [run, busy] = useAction();
  const [t, setT] = useState({ symbol: symbols[0] || "EURUSD", side: "BUY", stop_loss: "", take_profit: "", volume: "", note: "" });
  const [preview, setPreview] = useState(null);
  const d = digitsFor(t.symbol);
  const body = () => ({ symbol: t.symbol, side: t.side, stop_loss: Number(t.stop_loss), take_profit: t.take_profit ? Number(t.take_profit) : null,
    volume: t.volume ? Number(t.volume) : null, note: t.note });
  const suggest = () => run(() => api.get(`/live/suggest?symbol=${t.symbol}&side=${t.side}`)).then((s) => {
    setT((x) => ({ ...x, stop_loss: s.stop_loss.toFixed(d), take_profit: s.take_profit.toFixed(d) }));
    setPreview(null);
    toast(`Suggested from ATR (${s.timeframe}): entry ≈ ${s.entry}`);
  }).catch(() => {});
  useEffect(() => { suggest(); }, [t.symbol, t.side]); // eslint-disable-line
  const set = (k) => (e) => { setT({ ...t, [k]: e.target.value }); setPreview(null); };
  return (
    <Modal title="New order" onClose={onClose}>
      <div className="grid two">
        <Field label="Market"><select value={t.symbol} onChange={set("symbol")}>{symbols.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Direction">
          <div className="row gap">
            <button type="button" className={`btn ${t.side === "BUY" ? "buy" : "ghost"}`} onClick={() => { setT({ ...t, side: "BUY" }); setPreview(null); }}>▲ BUY</button>
            <button type="button" className={`btn ${t.side === "SELL" ? "sell" : "ghost"}`} onClick={() => { setT({ ...t, side: "SELL" }); setPreview(null); }}>▼ SELL</button>
          </div>
        </Field>
        <Field label="Stop-loss price (required)"><input type="number" step="any" value={t.stop_loss} onChange={set("stop_loss")} /></Field>
        <Field label="Take-profit price"><input type="number" step="any" value={t.take_profit} onChange={set("take_profit")} /></Field>
        <Field label="Lots (optional)" hint="Empty = size automatically from your risk per trade"><input type="number" step="0.01" value={t.volume} onChange={set("volume")} /></Field>
        <Field label="Note"><input value={t.note} onChange={set("note")} placeholder="Why this trade?" /></Field>
      </div>
      <div className="row gap"><button className="btn sm ghost" onClick={suggest}>↺ Suggest SL/TP from ATR</button></div>
      {preview && (
        <div className={`alert ${preview.approved ? "ok" : "bad"}`}>
          <div className="row between wrap gap">
            <b>{preview.approved ? "Risk checks passed" : "Rejected by the risk engine"}</b>
            <span>{preview.volume} lots · risk {fmt.money(preview.risk_amount)} of {fmt.money(preview.risk_budget)} · price {preview.entry_price} · mode {preview.mode}</span>
          </div>
          <ul className="checks small">{preview.checks.filter((c) => !c.passed).map((c, i) => <li key={i} className="bad"><span>✕</span> {c.check} <span className="muted">{c.detail}</span></li>)}</ul>
        </div>
      )}
      <div className="row end gap">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={busy || !t.stop_loss} onClick={() => run(() => api.post("/live/preview", body())).then(setPreview).catch(() => {})}>Check risk</button>
        <button className={`btn ${t.side === "BUY" ? "buy" : "sell"}`} disabled={busy || !preview?.approved}
          onClick={() => run(() => api.post("/live/order", body()), "Order sent").then((r) => { onPlaced(r); onClose(); }).catch(() => {})}>
          Place {t.side} {preview?.volume ?? ""}
        </button>
      </div>
      <p className="muted small">Every manual order passes the same risk engine as automatic trades: stop-loss required, size limited to your risk per trade, loss limits and kill switch respected.</p>
    </Modal>
  );
}

export default function Live() {
  const { can } = useAuth();
  const { data: f, reload } = useApi("/live/feed", { interval: 3000 });
  const { data: strat } = useApi("/system/strategy");
  const [run] = useAction();
  const [ticket, setTicket] = useState(false);
  const hist = useRef([]);
  const [spark, setSpark] = useState([]);
  useEffect(() => {
    if (f?.account) {
      hist.current = [...hist.current.slice(-120), { x: Date.now(), y: f.account.equity }];
      setSpark(hist.current);
    }
  }, [f]);
  const symbols = useMemo(() => strat?.symbols || ["EURUSD"], [strat]);
  if (!f) return <p className="muted">Loading…</p>;
  const a = f.account;
  const canTrade = ["PAPER", "MT5_DEMO", "MT5_LIVE"].includes(f.mode);
  return (
    <div className="stack">
      <div className="row between wrap gap">
        <div className="row gap wrap">
          <Badge tone={modeTone(f.mode)}>Mode: {f.mode}</Badge>
          <Badge tone="info">Broker: {f.broker_active}</Badge>
          {f.kill_switch && <Badge tone="bad">KILL SWITCH</Badge>}
          {f.locked && <Badge tone="bad">LOCKED</Badge>}
          <span className="muted small">updates every 3 s · {fmt.dt(f.server_time)}</span>
        </div>
        {can("trading.manual") && (
          <button className="btn primary" disabled={!canTrade} title={canTrade ? "" : "Switch mode to PAPER or BROKER DEMO on the Dashboard"} onClick={() => setTicket(true)}>+ New order</button>
        )}
      </div>
      {!canTrade && <div className="alert warn small">Trading mode is <b>{f.mode}</b>. To trade (automatically or manually), set the mode to <b>PAPER</b> or <b>BROKER DEMO</b> on the Dashboard.</div>}

      <div className="grid stats">
        <Stat label={`Equity (${f.account_mode})`} value={fmt.money(a?.equity, a?.currency)} sub={<Sparkline points={spark} />} />
        <Stat label="Balance" value={fmt.money(a?.balance)} sub="closed trades only" />
        <Stat label="Open P&L" value={<span className={pnlClass(f.unrealized)}>{fmt.money(f.unrealized)}</span>} sub={`${f.positions.length} open position(s)`} />
        <Stat label="Free margin" value={fmt.money(a?.free_margin)} sub={f.account_meta?.server || f.account_meta?.source} />
      </div>

      <Card title="Open positions (managed by the app)">
        <Table rows={f.positions} empty="No open positions" cols={[
          { label: "Symbol", k: "symbol" },
          { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
          { label: "Lots", k: "volume", num: true },
          { label: "Open", num: true, render: (p) => fmt.px(p.open_price, digitsFor(p.symbol)) },
          { label: "Now", num: true, render: (p) => fmt.px(p.current_price, digitsFor(p.symbol)) },
          { label: "SL", num: true, render: (p) => fmt.px(p.stop_loss, digitsFor(p.symbol)) },
          { label: "TP", num: true, render: (p) => fmt.px(p.take_profit, digitsFor(p.symbol)) },
          { label: "P&L", num: true, render: (p) => <b className={pnlClass(p.unrealized_pnl)}>{fmt.money(p.unrealized_pnl)}</b> },
          { label: "Opened", render: (p) => fmt.dt(p.opened_at) },
          { label: "", render: (p) => can("killswitch.use") && <button className="btn sm" onClick={() => window.confirm(`Close ${p.symbol} ${p.side} ${p.volume}?`) && run(() => api.post(`/positions/${p.id}/close`), "Close requested").then(reload)}>Close</button> },
        ]} />
      </Card>

      {f.external_positions?.length > 0 && (
        <Card title="Other positions on your broker account (opened outside the app — read only)">
          <Table rows={f.external_positions} rowKey="ticket" cols={[
            { label: "Ticket", k: "ticket" }, { label: "Symbol", k: "symbol" },
            { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
            { label: "Size", k: "size", num: true }, { label: "Open", k: "open_price", num: true },
            { label: "SL", render: (p) => p.sl || <span className="neg">none</span> }, { label: "TP", render: (p) => p.tp || "—" },
            { label: "P&L", num: true, render: (p) => <span className={pnlClass(p.pnl)}>{fmt.money(p.pnl)}</span> },
          ]} />
          <p className="muted small">The app does not manage or close these, and they don't count towards its risk limits. Manage them in your broker's app.</p>
        </Card>
      )}

      <div className="grid two">
        <Card title="Activity">
          <ul className="feed">
            {f.activity.length === 0 && <li className="muted">Nothing yet</li>}
            {f.activity.map((x, i) => (
              <li key={i}>
                <span className="muted small">{fmt.dt(x.at)}</span>
                <b>{ACTION_LABEL[x.action] || x.action}</b>
                <span className="small">{[x.details?.symbol, x.details?.side, x.details?.volume && `${x.details.volume} lots`, x.details?.pnl !== undefined && x.details?.pnl !== null && `P&L ${fmt.money(x.details.pnl)}`, x.details?.reason, x.details?.to && `→ ${x.details.to}`, x.details?.error].filter(Boolean).join(" · ")}</span>
                <span className="muted small">{x.user}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Recent orders & system events">
          <Table rows={f.orders.slice(0, 8)} rowKey="client_id" cols={[
            { label: "Time", render: (o) => fmt.dt(o.created_at) }, { label: "Order", render: (o) => `${o.action} ${o.side} ${o.volume} ${o.symbol}` },
            { label: "Status", render: (o) => <Badge tone={statusTone(o.status)} title={o.error}>{o.status}</Badge> },
            { label: "Fill", num: true, render: (o) => fmt.px(o.fill_price, digitsFor(o.symbol)) },
          ]} />
          <ul className="feed small">{f.events.slice(0, 6).map((e, i) => <li key={i}><span className="muted">{fmt.dt(e.at)}</span><Badge tone={{ ERROR: "bad", WARNING: "warn" }[e.level] || "muted"}>{e.level}</Badge><span>{e.message}</span></li>)}</ul>
        </Card>
      </div>
      {ticket && <OrderTicket symbols={symbols} onClose={() => setTicket(false)} onPlaced={() => reload()} />}
    </div>
  );
}
