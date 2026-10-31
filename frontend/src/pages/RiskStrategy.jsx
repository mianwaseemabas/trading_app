import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, PasswordPrompt, useAction, useApi } from "../components.jsx";

const RISK_LABELS = {
  reference_balance: ["Reference balance", "Risk is sized from min(equity, this). Prevents automatic risk growth."],
  risk_per_trade_pct: ["Risk per trade (%)", "Of equity; max 2%"],
  daily_loss_pct: ["Daily loss stop (%)", "Blocks new trades until manual review"],
  weekly_loss_pct: ["Weekly loss stop (%)", ""],
  max_concurrent_positions: ["Max concurrent positions", ""],
  max_positions_per_symbol: ["Max positions per symbol", ""],
  max_spread_points: ["Max spread (points)", "10 points = 1 pip on 5-digit pairs"],
  max_slippage_points: ["Max slippage (points)", "Also added to the risk calculation"],
  commission_per_lot: ["Commission per lot (round turn)", "Included in position sizing"],
  min_free_margin_pct: ["Min free margin after trade (% of equity)", ""],
  order_ttl_seconds: ["Order time-to-live (s)", "Unsent orders expire"],
  include_unrealized_in_limits: ["Include unrealized P&L in loss limits", ""],
};

function RiskForm() {
  const { can } = useAuth();
  const { data, reload } = useApi("/system/risk");
  const [vals, setVals] = useState(null);
  const [prompt, setPrompt] = useState(false);
  const [run, busy] = useAction();
  useEffect(() => { if (data) setVals(data.risk); }, [data]);
  if (!vals) return <Card title="Risk policy"><p className="muted">Loading…</p></Card>;
  const editable = can("risk.edit");
  const save = (password) => run(() => api.put("/system/risk", { values: vals, password }), "Risk settings saved").then(reload);
  const onSave = async () => {
    try { await save(undefined); } catch (e) { if (/Password/.test(e.message)) setPrompt(true); }
  };
  return (
    <Card title="Risk policy" actions={editable && <button className="btn primary sm" disabled={busy} onClick={onSave}>Save</button>}>
      <div className="row gap wrap small" style={{ marginBottom: 12 }}>
        <Badge tone="ok">Stop-loss mandatory</Badge><Badge tone="ok">Martingale disabled</Badge><Badge tone="ok">Grid / loss recovery disabled</Badge><Badge tone="ok">Averaging down disabled</Badge>
      </div>
      <div className="grid form">
        {Object.entries(RISK_LABELS).map(([k, [label, hint]]) => (
          <Field key={k} label={label} hint={hint || (data.bounds[k] ? `${data.bounds[k][0]} – ${data.bounds[k][1]}` : "")}>
            {typeof vals[k] === "boolean" ? (
              <select disabled={!editable} value={String(vals[k])} onChange={(e) => setVals({ ...vals, [k]: e.target.value === "true" })}><option value="true">Yes</option><option value="false">No</option></select>
            ) : (
              <input type="number" step="any" disabled={!editable} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
            )}
          </Field>
        ))}
      </div>
      <p className="muted small">Changes that increase risk require your password and are recorded in the audit log.</p>
      {prompt && <PasswordPrompt title="Confirm risk increase" message="One or more changes increase risk. Confirm with your password." onClose={() => setPrompt(false)} onSubmit={save} />}
    </Card>
  );
}

const STRAT_FIELDS = [
  ["ema_fast", "EMA fast"], ["ema_slow", "EMA slow"], ["rsi_period", "RSI period"],
  ["rsi_long_min", "RSI long min"], ["rsi_long_max", "RSI long max"], ["rsi_short_min", "RSI short min"], ["rsi_short_max", "RSI short max"],
  ["macd_fast", "MACD fast"], ["macd_slow", "MACD slow"], ["macd_signal", "MACD signal"],
  ["atr_period", "ATR period"], ["atr_stop_mult", "Stop = ATR ×"], ["reward_risk", "Take-profit = stop ×"], ["max_atr_pct", "Skip if ATR > % of price"],
];
const SMC_FIELDS = [
  ["smc_swing_n", "Swing = candles each side"], ["smc_displacement_atr", "Displacement body ≥ ATR ×"], ["smc_fvg_min_atr", "Min FVG size (ATR ×)"],
  ["smc_bos_window", "Max candles sweep → break"], ["smc_retest_window", "Max candles break → retest"], ["smc_max_stop_atr", "Max stop (ATR ×)"],
  ["smc_reward_risk", "SMC target = stop ×"],
];
const STRATS = [
  { k: "trend_pullback", label: "EMA trend pullback (baseline)", d: "LONG when EMA fast > EMA slow, close > EMA slow, MACD histogram crosses above zero, RSI within the long band and volatility is normal. SHORT is the mirror image." },
  { k: "smc_sweep_bos", label: "SMC: sweep → BOS/CHoCH → retest", d: "Waits for a liquidity sweep (wick beyond a swing level, close back inside), then a structure break with a displacement candle, then a retest of the fair value gap or order block. Stop beyond the sweep extreme; target = stop × reward." },
];
const TFS = ["M15", "M30", "H1", "H4"];

function StrategyForm() {
  const { can } = useAuth();
  const { data, reload } = useApi("/system/strategy");
  const [vals, setVals] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { if (data) setVals({ ...data, symbols: data.symbols.join(", ") }); }, [data]);
  if (!vals) return <Card title="Strategy"><p className="muted">Loading…</p></Card>;
  const editable = can("strategy.edit");
  const toggle = (k, tf) => setVals({ ...vals, [k]: vals[k].includes(tf) ? vals[k].filter((x) => x !== tf) : [...vals[k], tf] });
  const save = () => {
    const values = { ...vals, symbols: vals.symbols.split(",").map((s) => s.trim()).filter(Boolean) };
    delete values.version;
    run(() => api.put("/system/strategy", { values }), "Strategy saved").then(reload).catch(() => {});
  };
  return (
    <Card title={`Strategy: ${(STRATS.find((x) => x.k === data.name) || {}).label || data.name} · v${data.version}`} actions={editable && <button className="btn primary sm" disabled={busy} onClick={save}>Save</button>}>
      <Field label="Live strategy (what the engine trades)">
        <div className="seg">{STRATS.map((x) => <button key={x.k} type="button" disabled={!editable} className={vals.name === x.k ? "active" : ""} onClick={() => setVals({ ...vals, name: x.k })}>{x.label}</button>)}</div>
      </Field>
      <p className="muted small">{(STRATS.find((x) => x.k === vals.name) || STRATS[0]).d} Evaluated on closed candles only. Changing rule parameters bumps the version so every signal stays traceable. This is a hypothesis to test, not a proven edge.</p>
      <Field label="Symbols (comma separated, broker names)"><input disabled={!editable} value={vals.symbols} onChange={(e) => setVals({ ...vals, symbols: e.target.value.toUpperCase() })} /></Field>
      <div className="grid two">
        <Field label="Evaluate timeframes"><div className="row gap">{TFS.map((t) => <label key={t} className="check"><input type="checkbox" disabled={!editable} checked={vals.timeframes.includes(t)} onChange={() => toggle("timeframes", t)} />{t}</label>)}</div></Field>
        <Field label="Timeframes allowed to place orders"><div className="row gap">{TFS.map((t) => <label key={t} className="check"><input type="checkbox" disabled={!editable} checked={vals.trade_timeframes.includes(t)} onChange={() => toggle("trade_timeframes", t)} />{t}</label>)}</div></Field>
      </div>
      <div className="grid form">
        {STRAT_FIELDS.map(([k, label]) => (
          <Field key={k} label={label}><input type="number" step="any" disabled={!editable} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></Field>
        ))}
      </div>
      <h4>SMC rules (research definitions)</h4>
      <div className="grid form">
        {SMC_FIELDS.map(([k, label]) => (
          <Field key={k} label={label}><input type="number" step="any" disabled={!editable} value={vals[k] ?? ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></Field>
        ))}
        <Field label="Trend filter"><label className="check"><input type="checkbox" disabled={!editable} checked={!!vals.smc_trend_filter} onChange={(e) => setVals({ ...vals, smc_trend_filter: e.target.checked })} /> only trade on the side of EMA slow</label></Field>
      </div>
    </Card>
  );
}

function LiveGate() {
  const { can } = useAuth();
  const { data, reload } = useApi("/system/live-gate");
  const [items, setItems] = useState({});
  const [prompt, setPrompt] = useState(false);
  const [run] = useAction();
  useEffect(() => { if (data) setItems(Object.fromEntries(Object.keys(data.items).map((k) => [k, data.gate[k]]))); }, [data]);
  if (!data) return null;
  const editable = can("live.approve");
  const allOk = Object.values(items).every(Boolean);
  return (
    <Card title="Live-trading gate" actions={<Badge tone={data.gate.approved ? "warn" : "muted"}>{data.gate.approved ? `Approved by ${data.gate.approved_by}` : "Not approved"}</Badge>}>
      <p className="muted small">Broker Live mode stays unavailable until every item is confirmed and an authorised user approves with their password. Unchecking any item revokes approval and drops Live to Demo.</p>
      <ul className="gate">
        {Object.entries(data.items).map(([k, label]) => (
          <li key={k}><label className="check"><input type="checkbox" disabled={!editable} checked={!!items[k]} onChange={(e) => setItems({ ...items, [k]: e.target.checked })} /> {label}</label></li>
        ))}
      </ul>
      {editable && (
        <div className="row end gap">
          <button className="btn" onClick={() => run(() => api.put("/system/live-gate", { items }), "Checklist saved").then(reload)}>Save checklist</button>
          <button className="btn danger" disabled={!allOk || data.gate.approved} onClick={() => setPrompt(true)}>Approve live trading</button>
        </div>
      )}
      {prompt && <PasswordPrompt title="Approve live trading gate" message="You confirm all gates have passed and approve the live configuration." onClose={() => setPrompt(false)} onSubmit={(password) => run(() => api.put("/system/live-gate", { items, approve: true, password }), "Live gate approved").then(reload)} />}
    </Card>
  );
}

export default function RiskStrategy() {
  return (
    <div className="stack">
      <RiskForm />
      <StrategyForm />
      <LiveGate />
    </div>
  );
}
