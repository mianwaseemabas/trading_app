import React, { useState } from "react";
import { Badge, Card, Modal, Table, digitsFor, fmt, statusTone, useApi } from "../components.jsx";

function CheckList({ items }) {
  if (!items || !items.length) return <p className="muted">None</p>;
  return (
    <ul className="checks">
      {items.map((c, i) => (
        <li key={i} className={c.passed ? "ok" : "bad"}>
          <span>{c.passed ? "✓" : "✕"}</span> {c.side ? <b>{c.side}: </b> : null}{c.check}{c.detail ? <span className="muted"> — {c.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function SignalDetail({ id, onClose }) {
  const { data: s } = useApi(`/signals/${id}`);
  return (
    <Modal title={`Signal #${id}`} onClose={onClose} wide>
      {!s ? <p className="muted">Loading…</p> : (
        <div className="stack">
          <div className="row gap wrap">
            <Badge tone={statusTone(s.action)}>{s.action}</Badge>
            <Badge tone={statusTone(s.status)}>{s.status}</Badge>
            <span>{s.symbol} {s.timeframe} · candle {fmt.dt(s.candle_time)} · {s.strategy} v{s.strategy_version} · mode {s.trading_mode}</span>
          </div>
          {s.action !== "NONE" && (
            <div className="grid stats">
              <div className="stat"><div className="stat-l">Entry ref</div><div className="stat-v">{fmt.px(s.entry_ref, digitsFor(s.symbol))}</div></div>
              <div className="stat"><div className="stat-l">Stop-loss</div><div className="stat-v">{fmt.px(s.stop_loss, digitsFor(s.symbol))}</div></div>
              <div className="stat"><div className="stat-l">Take-profit</div><div className="stat-v">{fmt.px(s.take_profit, digitsFor(s.symbol))}</div></div>
              {s.features?.risk && <div className="stat"><div className="stat-l">Size / risk</div><div className="stat-v">{s.features.risk.volume} lots</div><div className="stat-s">{fmt.money(s.features.risk.risk_amount)} of {fmt.money(s.features.risk.risk_budget)} budget</div></div>}
            </div>
          )}
          <div className="grid two">
            <div><h4>Strategy conditions</h4><CheckList items={s.reasons} /></div>
            <div><h4>Risk checks</h4>{s.risk_checks?.length ? <CheckList items={s.risk_checks} /> : <p className="muted">Not evaluated (no trade proposed or mode does not place orders).</p>}</div>
          </div>
          <h4>Inputs (features)</h4>
          <div className="kv">
            {Object.entries(s.features || {}).filter(([k]) => !["risk", "gaps"].includes(k)).map(([k, v]) => (
              <div key={k}><span className="muted">{k}</span><span>{typeof v === "number" ? (Math.abs(v) < 0.01 ? v.toExponential(3) : v.toFixed(5)) : String(v)}</span></div>
            ))}
          </div>
          {s.orders?.length > 0 && (
            <>
              <h4>Orders</h4>
              <Table rows={s.orders} cols={[
                { label: "Client ID", k: "client_id" }, { label: "Mode", k: "mode" }, { label: "Volume", k: "volume", num: true },
                { label: "Status", render: (o) => <Badge tone={statusTone(o.status)}>{o.status}</Badge> },
                { label: "Fill", num: true, render: (o) => fmt.px(o.fill_price, digitsFor(o.symbol)) }, { label: "Error", k: "error" },
              ]} />
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

export default function Signals() {
  const [actionable, setActionable] = useState(true);
  const [status, setStatus] = useState("");
  const { data } = useApi(`/signals?limit=300${actionable ? "&actionable=true" : ""}${status ? `&status=${status}` : ""}`, { interval: 30000, deps: [actionable, status] });
  const [open, setOpen] = useState(null);
  return (
    <Card title="Signal log" actions={
      <>
        <label className="check"><input type="checkbox" checked={actionable} onChange={(e) => setActionable(e.target.checked)} /> BUY/SELL only</label>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {["ORDERED", "REJECTED", "SIGNAL_ONLY", "NO_TRADE"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </>
    }>
      <p className="muted small">Every evaluated closed candle is recorded with its inputs, the strategy conditions and the risk-engine checks. Click a row for details.</p>
      <Table rows={data} onRow={(r) => setOpen(r.id)} cols={[
        { label: "#", k: "id" },
        { label: "Candle", render: (r) => fmt.dt(r.candle_time) },
        { label: "Symbol", k: "symbol" },
        { label: "TF", k: "timeframe" },
        { label: "Action", render: (r) => <Badge tone={statusTone(r.action)}>{r.action}</Badge> },
        { label: "Entry", num: true, render: (r) => fmt.px(r.entry_ref, digitsFor(r.symbol)) },
        { label: "SL", num: true, render: (r) => fmt.px(r.stop_loss, digitsFor(r.symbol)) },
        { label: "TP", num: true, render: (r) => fmt.px(r.take_profit, digitsFor(r.symbol)) },
        { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
        { label: "Mode", k: "trading_mode" },
        { label: "Version", k: "strategy_version" },
      ]} />
      {open && <SignalDetail id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
