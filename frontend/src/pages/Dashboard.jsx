import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Meter, Modal, PasswordPrompt, Stat, Table, digitsFor, fmt, modeTone, pnlClass, statusTone, useAction, useApi } from "../components.jsx";

const MODES = [
  { k: "OFF", d: "Engine idle. No signals, no orders." },
  { k: "SIGNALS_ONLY", d: "Generate and log signals only." },
  { k: "PAPER", d: "Simulated execution against live quotes (internal paper account)." },
  { k: "MT5_DEMO", d: "Real orders on the MT5 DEMO account via the Windows bridge." },
  { k: "MT5_LIVE", d: "Real money. Requires approved live gate." },
];

function ModeSwitcher({ status, reload }) {
  const { can } = useAuth();
  const [run] = useAction();
  const [target, setTarget] = useState(null);
  const [confirm, setConfirm] = useState("");
  const cur = status.trading.mode;
  const choose = (m) => {
    if (m === cur) return;
    if (m.startsWith("MT5")) setTarget(m);
    else run(() => api.post("/system/mode", { mode: m }), `Mode set to ${m}`).then(reload).catch(() => {});
  };
  return (
    <>
      <div className="mode-grid">
        {MODES.map((m) => (
          <button key={m.k} className={`mode ${cur === m.k ? "on" : ""} ${m.k === "MT5_LIVE" ? "danger" : ""}`} disabled={!can("trading.control") || (m.k === "MT5_LIVE" && !status.live_gate.approved)} onClick={() => choose(m.k)} title={m.d}>
            <strong>{m.k.replace("_", " ")}</strong>
            <span>{m.d}</span>
          </button>
        ))}
      </div>
      {target && (
        <PasswordPrompt
          title={`Switch to ${target}`}
          message={target === "MT5_LIVE" ? "This enables automated trading with real money. Losses can exceed expectations." : "Orders will be sent to the MT5 demo account through the bridge."}
          extra={target === "MT5_LIVE" && (
            <Field label='Type "ENABLE LIVE TRADING"'><input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
          )}
          onClose={() => { setTarget(null); setConfirm(""); }}
          onSubmit={(password) => run(() => api.post("/system/mode", { mode: target, password, confirm }), `Mode set to ${target}`).then(reload)}
        />
      )}
    </>
  );
}

function KillSwitch({ status, reload }) {
  const { can } = useAuth();
  const [run, busy] = useAction();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [closeAll, setCloseAll] = useState(false);
  const on = status.trading.kill_switch;
  if (!can("killswitch.use")) return null;
  return (
    <>
      {on ? (
        <button className="btn" disabled={busy} onClick={() => run(() => api.post("/system/kill-switch", { engaged: false }), "Kill switch released").then(reload)}>Release kill switch</button>
      ) : (
        <button className="btn danger big" onClick={() => setOpen(true)}>■ KILL SWITCH</button>
      )}
      {open && (
        <Modal title="Engage kill switch" onClose={() => setOpen(false)}>
          <p>Blocks all new orders immediately and cancels pending orders.</p>
          <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. unexpected behaviour" /></Field>
          <label className="check"><input type="checkbox" checked={closeAll} onChange={(e) => setCloseAll(e.target.checked)} /> Also close all open positions at market</label>
          <div className="row end gap">
            <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn danger" disabled={busy} onClick={() => run(() => api.post("/system/kill-switch", { engaged: true, reason, close_positions: closeAll }), "Kill switch ENGAGED").then(() => { setOpen(false); reload(); })}>Engage</button>
          </div>
        </Modal>
      )}
    </>
  );
}

function Unlock({ status, reload }) {
  const { can } = useAuth();
  const [run] = useAction();
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  if (!status.trading.locked && status.reconciliation.ok) return null;
  return (
    <div className="alert bad">
      <div className="row between wrap gap">
        <div>
          <strong>New trades are blocked.</strong> {status.trading.lock_reason || status.reconciliation.detail}
        </div>
        {can("trading.control") && <button className="btn sm" onClick={() => setOpen(true)}>Review & reset</button>}
      </div>
      {open && (
        <Modal title="Manual review & reset" onClose={() => setOpen(false)}>
          <p className="muted">Confirm you have reviewed the cause. If a loss limit is still exceeded, the engine will lock again automatically until the next day/week.</p>
          <Field label="Review note (recorded in the audit log)"><textarea value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <div className="row end"><button className="btn primary" disabled={note.length < 3} onClick={() => run(() => api.post("/system/unlock", { note }), "Lock cleared").then(() => { setOpen(false); reload(); })}>Reset lock</button></div>
        </Modal>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { can } = useAuth();
  const { data: s, reload, error } = useApi("/system/status", { interval: 5000 });
  const { data: positions, reload: reloadPos } = useApi(can("trades.view") ? "/positions?status=OPEN" : null, { interval: 5000 });
  const { data: signals } = useApi(can("signals.view") ? "/signals?actionable=true&limit=8" : null, { interval: 15000 });
  const { data: quotes } = useApi(can("market.view") ? "/market/quotes" : null, { interval: 5000 });
  const [run] = useAction();
  if (error) return <div className="alert bad">{error}</div>;
  if (!s) return <div className="muted">Loading…</div>;
  const a = s.account;
  const ccy = a?.currency || "USD";
  const bridgeOk = s.bridge.age_s !== null && s.bridge.age_s < 90;
  return (
    <div className="stack">
      {s.data.source === "simulator" && <div className="alert warn">Data source is the <b>simulator</b> (synthetic prices for testing the system). Switch to MT5 in System settings to use broker data.</div>}
      <Unlock status={s} reload={reload} />
      {s.trading.kill_switch && <div className="alert bad"><strong>Kill switch engaged.</strong> {s.trading.kill_switch_reason}</div>}

      <div className="grid stats">
        <Stat label={`Equity (${s.account_mode})`} value={fmt.money(a?.equity, ccy)} sub={`Balance ${fmt.money(a?.balance)}`} />
        <Stat label="Free margin" value={fmt.money(a?.free_margin)} sub={`Margin ${fmt.money(s.account_meta?.margin)}`} />
        <Stat label="Realized today" value={<span className={pnlClass(s.realized_today)}>{fmt.money(s.realized_today)}</span>} sub="net of costs" />
        <Stat label="Unrealized" value={<span className={pnlClass(s.unrealized)}>{fmt.money(s.unrealized)}</span>} sub={`${s.open_positions} open position(s) · not profit until closed`} />
        <Stat label="MT5 bridge" value={<Badge tone={bridgeOk ? "ok" : s.bridge.configured ? "bad" : "muted"}>{bridgeOk ? "Connected" : s.bridge.configured ? "Offline" : "Not configured"}</Badge>} sub={`Last seen ${fmt.ago(s.bridge.age_s)}${s.account_meta?.trade_mode ? " · " + s.account_meta.trade_mode : ""}`} />
        <Stat label="Strategy" value={`${s.strategy.name} v${s.strategy.version}`} sub={`${s.strategy.symbols.join(", ")} · trades on ${s.strategy.trade_timeframes.join(", ")}`} />
      </div>

      <div className="grid two">
        <Card title="Trading mode" actions={<KillSwitch status={s} reload={reload} />}>
          <ModeSwitcher status={s} reload={reload} />
        </Card>
        <Card title="Loss limits" actions={<Badge tone={s.trading.locked ? "bad" : "ok"}>{s.trading.locked ? "Locked" : "Within limits"}</Badge>}>
          {s.limits ? (
            <div className="stack">
              <Meter label="Daily loss (UTC day)" value={s.limits.day_loss} max={s.limits.day_limit} />
              <Meter label="Weekly loss (ISO week)" value={s.limits.week_loss} max={s.limits.week_limit} />
              <p className="muted small">Limits are measured from start-of-period equity and include unrealized losses when configured. Breaches block new trades until a manual review.</p>
            </div>
          ) : <p className="muted">No account data for this mode yet.</p>}
        </Card>
      </div>

      {can("trades.view") && (
        <Card title="Open positions">
          <Table rows={positions} empty="No open positions" cols={[
            { label: "Mode", render: (p) => <Badge tone={modeTone(p.mode)}>{p.mode}</Badge> },
            { label: "Symbol", k: "symbol" },
            { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
            { label: "Volume", k: "volume", num: true },
            { label: "Open", num: true, render: (p) => fmt.px(p.open_price, digitsFor(p.symbol)) },
            { label: "Current", num: true, render: (p) => fmt.px(p.current_price, digitsFor(p.symbol)) },
            { label: "SL", num: true, render: (p) => fmt.px(p.stop_loss, digitsFor(p.symbol)) },
            { label: "TP", num: true, render: (p) => fmt.px(p.take_profit, digitsFor(p.symbol)) },
            { label: "Unrealized", num: true, render: (p) => <span className={pnlClass(p.unrealized_pnl)}>{fmt.money(p.unrealized_pnl)}</span> },
            { label: "Opened", render: (p) => fmt.dt(p.opened_at) },
            { label: "", render: (p) => can("killswitch.use") && <button className="btn sm" onClick={() => window.confirm(`Close ${p.symbol} ${p.side} ${p.volume}?`) && run(() => api.post(`/positions/${p.id}/close`), "Close requested").then(reloadPos)}>Close</button> },
          ]} />
        </Card>
      )}

      <div className="grid two">
        {can("signals.view") && (
          <Card title="Latest trade signals" actions={<a href="#/signals" className="small">All signals →</a>}>
            <Table rows={signals} empty="No BUY/SELL signals yet" cols={[
              { label: "Time", render: (r) => fmt.dt(r.candle_time) },
              { label: "Symbol", render: (r) => `${r.symbol} ${r.timeframe}` },
              { label: "Action", render: (r) => <Badge tone={statusTone(r.action)}>{r.action}</Badge> },
              { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
            ]} />
          </Card>
        )}
        {can("market.view") && (
          <Card title="Quotes">
            <Table rows={quotes} rowKey="symbol" empty="No quotes yet — waiting for the MT5 bridge" cols={[
              { label: "Symbol", k: "symbol" },
              { label: "Bid", num: true, render: (q) => fmt.px(q.bid, digitsFor(q.symbol)) },
              { label: "Ask", num: true, render: (q) => fmt.px(q.ask, digitsFor(q.symbol)) },
              { label: "Spread (pts)", num: true, k: "spread_points" },
              { label: "Age", render: (q) => <span className={q.age_s > 120 ? "neg" : ""}>{fmt.ago(q.age_s)}</span> },
              { label: "Source", render: (q) => <Badge tone={q.source === "simulator" ? "warn" : "info"}>{q.source}</Badge> },
            ]} />
          </Card>
        )}
      </div>
    </div>
  );
}
