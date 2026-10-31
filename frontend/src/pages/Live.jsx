import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import {
  Badge, Card, Field, LevelChart, Modal, Stat, Switch, Table, countdown, digitsFor, fmt, impactTone, modeLabel, modeTone,
  pnlClass, statusTone, useAction, useApi, useToast,
} from "../components.jsx";

const ACTION_LABEL = {
  "order.created": "Order created", "order.filled": "Order filled", "order.rejected": "Order rejected", "position.closed": "Position closed",
  "order.manual": "Manual order", "trading.kill_switch": "Kill switch", "trading.mode_changed": "Mode changed", "risk.locked": "Loss limit lock",
  "pending.created": "Pending order placed", "pending.triggered": "Pending order triggered", "pending.cancelled": "Pending order cancelled",
  "position.sl_tp_modified": "Stop / target moved", "controls.updated": "Controls changed",
};
const ORDER_MODES = ["PAPER", "MT5_DEMO", "MT5_LIVE"];

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

// ------------------------------------------------------------------ controls
function ControlBar({ f, symbols, reload }) {
  const { can } = useAuth();
  const [run, busy] = useAction();
  const [ks, setKs] = useState(false);
  const c = f.controls || {};
  const editable = can("trading.control");
  const put = (patch, msg) => run(() => api.put("/live/controls", patch), msg).then(reload).catch(() => {});
  const disabled = new Set(c.disabled_symbols || []);
  const togglePair = (s) => {
    const next = new Set(disabled);
    next.has(s) ? next.delete(s) : next.add(s);
    put({ disabled_symbols: [...next] }, `${s} ${next.has(s) ? "switched off" : "switched on"}`);
  };
  return (
    <Card title="Trading controls" actions={can("killswitch.use") && (f.kill_switch
      ? <button className="btn" disabled={busy} onClick={() => run(() => api.post("/system/kill-switch", { engaged: false }), "Kill switch released").then(reload)}>Release kill switch</button>
      : <button className="btn danger" onClick={() => setKs(true)}>■ KILL SWITCH</button>)}>
      <div className="controls-grid">
        <Switch on={c.auto_trading} disabled={!editable || busy} label="Strategy trading" hint="When off, the strategy only shows signals — it never opens trades by itself"
          onChange={(v) => put({ auto_trading: v }, v ? "Strategy trading ON" : "Strategy trading OFF — signals only")} />
        <div className="small"><b>Trade without asking me?</b><div className="tiny muted">Set per account (practice, demo, crypto, live) in <a href="#/lab">Strategy lab → Auto-trade</a>.</div></div>
        <Switch on={c.manual_trading} disabled={!editable || busy} label="Manual trading (my orders)" hint="When off, nobody can place manual orders"
          onChange={(v) => put({ manual_trading: v }, v ? "Manual trading ON" : "Manual trading OFF")} />
        <Switch on={c.news_filter} disabled={!editable || busy} label={`News filter (${c.news_before_min}m before / ${c.news_after_min}m after)`}
          hint="Blocks new trades on a currency around high-impact news" onChange={(v) => put({ news_filter: v }, v ? "News filter ON" : "News filter OFF")} />
        <div className="row gap wrap">
          <Switch on={c.session_filter} disabled={!editable || busy} label="Trading hours only" hint="Only open new trades between these hours (UTC)"
            onChange={(v) => put({ session_filter: v }, v ? "Trading hours filter ON" : "Trading hours filter OFF")} />
          {c.session_filter && (
            <span className="small muted">
              <select disabled={!editable} value={c.session_start_utc} onChange={(e) => put({ session_start_utc: Number(e.target.value) })}>{[...Array(24).keys()].map((h) => <option key={h} value={h}>{h}:00</option>)}</select>
              {" → "}
              <select disabled={!editable} value={c.session_end_utc} onChange={(e) => put({ session_end_utc: Number(e.target.value) })}>{[...Array(25).keys()].map((h) => <option key={h} value={h}>{h}:00</option>)}</select> UTC
            </span>
          )}
        </div>
      </div>
      <div className="row gap wrap" style={{ marginTop: 10 }}>
        <span className="small muted">Markets (click to switch on/off):</span>
        <div className="chips">
          {symbols.map((s) => <button key={s} disabled={!editable} className={`chip ${disabled.has(s) ? "off" : "on"}`} onClick={() => togglePair(s)}>{s}</button>)}
        </div>
      </div>
      <div className="row gap wrap small" style={{ marginTop: 10 }}>
        <span className="muted">Auto-protect every new trade:</span>
        <select disabled={!editable} value={c.default_breakeven_r ?? ""} onChange={(e) => put({ default_breakeven_r: e.target.value ? Number(e.target.value) : null }, "Default saved")}>
          <option value="">no break-even</option>{[0.5, 1, 1.5, 2].map((v) => <option key={v} value={v}>stop to break-even at +{v}R</option>)}
        </select>
        <select disabled={!editable} value={c.default_trailing_atr ?? ""} onChange={(e) => put({ default_trailing_atr: e.target.value ? Number(e.target.value) : null }, "Default saved")}>
          <option value="">no trailing stop</option>{[1, 1.5, 2, 3].map((v) => <option key={v} value={v}>trail stop {v}× ATR</option>)}
        </select>
      </div>
      {!editable && <p className="muted small">You can see the controls but your account can't change them.</p>}
      {ks && <KillSwitchModal onClose={() => setKs(false)} onDone={reload} />}
    </Card>
  );
}

function KillSwitchModal({ onClose, onDone }) {
  const [run, busy] = useAction();
  const [reason, setReason] = useState("");
  const [closeAll, setCloseAll] = useState(false);
  return (
    <Modal title="Engage kill switch" onClose={onClose}>
      <p>Blocks every new order immediately (automatic and manual). Pending orders are cancelled.</p>
      <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. too volatile" /></Field>
      <label className="row gap small"><input type="checkbox" checked={closeAll} onChange={(e) => setCloseAll(e.target.checked)} /> Also close all open positions now</label>
      <div className="row end">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={busy} onClick={() => run(() => api.post("/system/kill-switch", { engaged: true, reason: reason || "manual", close_positions: closeAll }), "Kill switch ENGAGED").then(() => { onClose(); onDone(); })}>Engage</button>
      </div>
    </Modal>
  );
}

// -------------------------------------------------------------- order ticket
const SIZING = [
  { k: "auto", label: "Auto", hint: "Uses your default risk per trade from Risk & Strategy" },
  { k: "risk_pct", label: "Risk %", hint: "Lose at most this % of your account if the stop-loss is hit", unit: "%", def: 0.5, step: 0.1 },
  { k: "risk_amount", label: "Risk $", hint: "Lose at most this amount if the stop-loss is hit (your 'limit amount')", unit: "$", def: 5, step: 1 },
  { k: "lots", label: "Lots", hint: "Fixed size — still capped so you never risk more than your risk-per-trade limit", unit: "lots", def: 0.01, step: 0.01 },
];
const TYPES = [
  { k: "MARKET", label: "Market", hint: "Buy/sell right now at the current price" },
  { k: "LIMIT", label: "Limit", hint: "Wait for a BETTER price: buy below / sell above the current price" },
  { k: "STOP", label: "Stop", hint: "Wait for a BREAKOUT: buy above / sell below the current price" },
];

function OrderTicket({ symbols, mode, onClose, onPlaced }) {
  const toast = useToast();
  const [run, busy] = useAction();
  const [t, setT] = useState({ symbol: symbols[0] || "EURUSD", side: "BUY", order_type: "MARKET", trigger_price: "", stop_loss: "", take_profit: "",
    sizing_mode: "risk_amount", sizing_value: "", breakeven_r: "", trailing_atr: "", expires_hours: "24", note: "" });
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  const d = digitsFor(t.symbol);
  const sizing = SIZING.find((s) => s.k === t.sizing_mode);
  const body = () => ({
    symbol: t.symbol, side: t.side, order_type: t.order_type, trigger_price: t.order_type === "MARKET" ? null : Number(t.trigger_price) || null,
    stop_loss: Number(t.stop_loss), take_profit: t.take_profit ? Number(t.take_profit) : null,
    sizing_mode: t.sizing_mode, sizing_value: t.sizing_mode === "auto" ? null : Number(t.sizing_value) || null,
    management: { breakeven_r: t.breakeven_r ? Number(t.breakeven_r) : null, trailing_atr: t.trailing_atr ? Number(t.trailing_atr) : null },
    expires_hours: t.order_type === "MARKET" ? null : Number(t.expires_hours) || 24, note: t.note,
  });
  const suggest = (entry) => api.get(`/live/suggest?symbol=${t.symbol}&side=${t.side}${entry ? `&entry=${entry}` : ""}`).then((s) => {
    setT((x) => ({ ...x, stop_loss: s.stop_loss.toFixed(d), take_profit: s.take_profit.toFixed(d), trigger_price: x.order_type === "MARKET" ? "" : (x.trigger_price || String(s.entry)) }));
  }).catch((e) => setErr(e.message));
  useEffect(() => { suggest(); }, [t.symbol, t.side]); // eslint-disable-line
  // live preview (debounced) — shows money at risk before you click
  useEffect(() => {
    setPreview(null);
    if (!t.stop_loss || (t.order_type !== "MARKET" && !t.trigger_price)) return undefined;
    if (t.sizing_mode !== "auto" && !t.sizing_value) {  // first open: fill "Risk $" with your maximum allowed risk
      api.post("/live/preview", { ...body(), sizing_mode: "auto", sizing_value: null }).then((p) => setT((x) => (x.sizing_value ? x : { ...x, sizing_value: String(Math.floor(p.risk_budget * 100) / 100) }))).catch((e) => setErr(e.message));
      return undefined;
    }
    const h = setTimeout(() => api.post("/live/preview", body()).then((p) => { setPreview(p); setErr(""); }).catch((e) => setErr(e.message)), 450);
    return () => clearTimeout(h);
  }, [JSON.stringify(t)]); // eslint-disable-line
  const set = (k) => (e) => setT({ ...t, [k]: e.target.value });
  const failed = preview?.checks?.filter((c) => !c.passed) || [];
  const place = () => run(() => api.post("/live/order", body())).then((r) => {
    toast(r.type === "MARKET" ? `Order sent: ${t.side} ${r.volume} ${t.symbol} (${r.status})` : `${r.type} order placed — waiting for ${t.trigger_price}`);
    onPlaced(r); onClose();
  }).catch(() => {});
  return (
    <Modal title={`New order · ${modeLabel(mode)}`} onClose={onClose} wide>
      {mode === "PAPER" && <div className="alert info small">PRACTICE mode: this trade uses virtual money at real market prices. Nothing is sent to a broker.</div>}
      {mode === "MT5_LIVE" && <div className="alert bad small"><b>REAL MONEY.</b> This order goes to your live broker account.</div>}
      <div className="grid two">
        <Field label="Market"><select value={t.symbol} onChange={set("symbol")}>{symbols.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Direction">
          <div className="row gap">
            <button type="button" className={`btn ${t.side === "BUY" ? "buy" : "ghost"}`} onClick={() => setT({ ...t, side: "BUY" })}>▲ BUY (price goes up)</button>
            <button type="button" className={`btn ${t.side === "SELL" ? "sell" : "ghost"}`} onClick={() => setT({ ...t, side: "SELL" })}>▼ SELL (price goes down)</button>
          </div>
        </Field>
        <Field label="Order type" hint={TYPES.find((x) => x.k === t.order_type).hint}>
          <div className="seg">{TYPES.map((x) => <button key={x.k} type="button" className={t.order_type === x.k ? "active" : ""} onClick={() => setT({ ...t, order_type: x.k, trigger_price: x.k === "MARKET" ? "" : t.trigger_price })}>{x.label}</button>)}</div>
        </Field>
        {t.order_type !== "MARKET" ? (
          <Field label={`${t.order_type === "LIMIT" ? "Limit" : "Stop"} price (order triggers here)`} hint={preview ? `Now: bid ${fmt.px(preview.bid, d)} / ask ${fmt.px(preview.ask, d)}` : ""}>
            <div className="row gap"><input type="number" step="any" value={t.trigger_price} onChange={set("trigger_price")} />
              <button type="button" className="btn sm ghost" onClick={() => suggest(t.trigger_price)}>SL/TP from here</button></div>
          </Field>
        ) : <Field label="Current price">{preview ? <b className="mono">bid {fmt.px(preview.bid, d)} · ask {fmt.px(preview.ask, d)}</b> : <span className="muted">…</span>}</Field>}
        <Field label="Stop-loss price (required)" hint="Where the trade closes automatically if you're wrong"><input type="number" step="any" value={t.stop_loss} onChange={set("stop_loss")} /></Field>
        <Field label="Take-profit price" hint="Where the trade closes automatically in profit"><input type="number" step="any" value={t.take_profit} onChange={set("take_profit")} /></Field>
        <Field label="Trade size" hint={<>{sizing.hint}{preview ? <> · <b>your limit: {fmt.money(preview.risk_budget)} per trade</b> (Risk & Strategy)</> : null}</>}>
          <div className="row gap wrap">
            <div className="seg">{SIZING.map((s) => <button key={s.k} type="button" className={t.sizing_mode === s.k ? "active" : ""} onClick={() => setT({ ...t, sizing_mode: s.k, sizing_value: s.k === "risk_amount" ? "" : s.def ? String(s.def) : "" })}>{s.label}</button>)}</div>
            {t.sizing_mode !== "auto" && <span className="row gap"><input style={{ width: 90 }} type="number" step={sizing.step} min="0" value={t.sizing_value} onChange={set("sizing_value")} /> <span className="muted small">{sizing.unit}</span></span>}
          </div>
        </Field>
        <Field label="Note"><input value={t.note} onChange={set("note")} placeholder="Why this trade? (helps your journal)" /></Field>
        <Field label="Auto-protect" hint="Moves your stop automatically as the trade goes in your favour (never widens it)">
          <div className="row gap wrap">
            <select value={t.breakeven_r} onChange={set("breakeven_r")}><option value="">default break-even</option>{[0.5, 1, 1.5, 2].map((v) => <option key={v} value={v}>break-even at +{v}R</option>)}</select>
            <select value={t.trailing_atr} onChange={set("trailing_atr")}><option value="">default trailing</option>{[1, 1.5, 2, 3].map((v) => <option key={v} value={v}>trail {v}× ATR</option>)}</select>
          </div>
        </Field>
        {t.order_type !== "MARKET" && (
          <Field label="Cancel the order if not triggered within">
            <select value={t.expires_hours} onChange={set("expires_hours")}>{[[1, "1 hour"], [4, "4 hours"], [24, "1 day"], [72, "3 days"], [168, "1 week"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </Field>
        )}
      </div>
      <div className="row gap"><button className="btn sm ghost" onClick={() => suggest(t.trigger_price)}>↺ Suggest stop & target from volatility (ATR)</button></div>

      {err && !preview && <div className="alert bad small">{err}</div>}
      {preview && (
        <>
          <div className="outcome">
            <div><div className="lbl">Size</div><div className="val">{preview.volume || 0} lots</div><div className="small muted">max allowed {preview.max_volume ?? "—"}</div></div>
            <div><div className="lbl">If stop-loss hits</div><div className="val neg">−{fmt.money(preview.loss_if_sl)}</div><div className="small muted">{preview.equity ? fmt.pct((preview.loss_if_sl / preview.equity) * 100) : ""} of account</div></div>
            <div><div className="lbl">If take-profit hits</div><div className="val pos">{preview.gain_if_tp != null ? `+${fmt.money(preview.gain_if_tp)}` : "—"}</div></div>
            <div><div className="lbl">Reward : risk</div><div className={`val ${preview.reward_risk >= 1.5 ? "pos" : "neg"}`}>{preview.reward_risk != null ? `${preview.reward_risk} : 1` : "—"}</div><div className="small muted">aim for 1.5+</div></div>
            <div><div className="lbl">Value of 1 pip</div><div className="val">{fmt.money(preview.pip_value)}</div></div>
          </div>
          <div className={`alert ${preview.approved ? "ok" : "bad"} small`}>
            <b>{preview.approved ? "✓ All risk checks passed" : "✕ Blocked by the risk engine"}</b>
            {failed.length > 0 && <ul className="checks">{failed.map((c, i) => <li key={i} className="bad"><span>✕</span> {c.check} <span className="muted">{c.detail}</span></li>)}</ul>}
          </div>
        </>
      )}
      <div className="row end gap">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className={`btn ${t.side === "BUY" ? "buy" : "sell"}`} disabled={busy || !preview?.approved} onClick={place}>
          {t.order_type === "MARKET" ? `Place ${t.side} ${preview?.volume ?? ""} now` : `Place ${t.side} ${t.order_type} @ ${t.trigger_price}`}
        </button>
      </div>
      <p className="muted small">Every order passes the same risk engine as automatic trades: stop-loss required, size capped by your risk limit, daily/weekly loss limits, news & hours filters and the kill switch.</p>
    </Modal>
  );
}

// ----------------------------------------------------------- journal screenshots
function Screenshots({ pid, items, onChange }) {
  const [run, busy] = useAction();
  const [big, setBig] = useState(null);
  const upload = (file) => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    run(() => api.upload(`/positions/${pid}/attachments`, fd), "Screenshot added").then(onChange).catch(() => {});
  };
  return (
    <div style={{ marginTop: 10 }}>
      <div className="row between"><b className="small">Screenshots</b>
        <label className="btn sm ghost" style={{ cursor: "pointer" }}>{busy ? "Uploading…" : "+ Add screenshot"}<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" style={{ display: "none" }} onChange={(e) => upload(e.target.files[0])} /></label>
      </div>
      <div className="row gap wrap" style={{ marginTop: 6 }}>
        {items.length === 0 && <span className="muted small">Add your chart screenshot from TradingView or the broker app.</span>}
        {items.map((a) => (
          <div key={a.id} style={{ position: "relative" }}>
            <img src={`/api/positions/attachments/${a.id}`} alt={a.filename} title={a.filename} onClick={() => setBig(a.id)} style={{ width: 120, height: 80, objectFit: "cover", borderRadius: 6, border: "1px solid var(--line2)", cursor: "zoom-in" }} />
            <button className="btn sm ghost" style={{ position: "absolute", top: 2, right: 2, padding: "0 6px" }} title="Delete" onClick={() => window.confirm("Delete this screenshot?") && run(() => api.del(`/positions/attachments/${a.id}`), "Deleted").then(onChange)}>×</button>
          </div>
        ))}
      </div>
      {big && <Modal title="Screenshot" onClose={() => setBig(null)} wide><img src={`/api/positions/attachments/${big}`} alt="" style={{ maxWidth: "100%" }} /></Modal>}
    </div>
  );
}

// ----------------------------------------------------------- position detail
export function PositionDetail({ id, onClose, onChanged }) {
  const { can } = useAuth();
  const { data, reload } = useApi(`/positions/${id}/detail`, { interval: 5000, deps: [id] });
  const [run, busy] = useAction();
  const [edit, setEdit] = useState(null);
  const [note, setNote] = useState(null);
  useEffect(() => {
    if (data && edit === null) setEdit({ sl: data.position.stop_loss ?? "", tp: data.position.take_profit ?? "", be: data.management?.breakeven_r ?? "", tr: data.management?.trailing_atr ?? "" });
    if (data && note === null) setNote({ note: data.note?.note || "", tags: (data.note?.tags || []).join(", "), rating: data.note?.rating || 0 });
  }, [data]); // eslint-disable-line
  if (!data) return <Modal title="Trade" onClose={onClose}><p className="muted">Loading…</p></Modal>;
  const p = data.position, dg = digitsFor(p.symbol), open = p.status === "OPEN";
  const pnl = open ? p.unrealized_pnl : p.realized_pnl;
  const levels = [
    { label: "Entry", price: p.open_price, color: "var(--text)" },
    { label: "SL", price: p.stop_loss, color: "var(--bad)" },
    { label: "TP", price: p.take_profit, color: "var(--ok)" },
    ...(data.management && data.management.initial_sl !== p.stop_loss ? [{ label: "first SL", price: data.management.initial_sl, color: "var(--bad)", dash: true }] : []),
    ...(p.close_price ? [{ label: "Exit", price: p.close_price, color: "var(--warn)", dash: true }] : []),
  ];
  const marks = [{ time: new Date(p.opened_at).getTime() / 1000, label: "open" }, ...(p.closed_at ? [{ time: new Date(p.closed_at).getTime() / 1000, label: "close", color: "var(--warn)" }] : [])];
  const done = () => { reload(); onChanged && onChanged(); };
  return (
    <Modal title={`${p.symbol} ${p.side} ${p.volume} lots · ${modeLabel(p.mode)} · ${p.status}`} onClose={onClose} wide>
      <div className="grid stats">
        <Stat label={open ? "Open P&L" : "Result"} value={<span className={pnlClass(pnl)}>{fmt.money(pnl)}</span>} sub={data.r_multiple != null ? `${data.r_multiple > 0 ? "+" : ""}${data.r_multiple} R` : ""} />
        <Stat label="Entry → now" value={`${fmt.px(p.open_price, dg)} → ${fmt.px(open ? p.current_price : p.close_price, dg)}`} sub={open ? "" : p.close_reason} />
        <Stat label="Duration" value={data.duration_min < 120 ? `${data.duration_min} min` : `${(data.duration_min / 60).toFixed(1)} h`} sub={fmt.dt(p.opened_at)} />
        <Stat label="Costs" value={fmt.money((p.commission || 0) + (p.swap || 0))} sub="commission + swap" />
      </div>
      <LevelChart candles={data.candles} levels={levels} digits={dg} marks={marks} />

      {open && can("trading.manual") && edit && (
        <div className="grid two">
          <Card title="Move stop-loss / take-profit">
            <div className="grid two">
              <Field label="Stop-loss" hint="Can only be moved to reduce risk"><input type="number" step="any" value={edit.sl} onChange={(e) => setEdit({ ...edit, sl: e.target.value })} /></Field>
              <Field label="Take-profit" hint="Empty = remove target"><input type="number" step="any" value={edit.tp} onChange={(e) => setEdit({ ...edit, tp: e.target.value })} /></Field>
            </div>
            <div className="row gap wrap">
              <button className="btn sm ghost" onClick={() => setEdit({ ...edit, sl: p.open_price })}>Stop to break-even</button>
              <button className="btn primary sm" disabled={busy} onClick={() => run(() => api.post(`/positions/${p.id}/modify`, { stop_loss: Number(edit.sl) || null, take_profit: edit.tp === "" ? 0 : Number(edit.tp) }),
                "Change requested").then(done).catch(() => {})}>Save levels</button>
            </div>
          </Card>
          <Card title="Auto-protect">
            <div className="grid two">
              <Field label="Break-even"><select value={edit.be} onChange={(e) => setEdit({ ...edit, be: e.target.value })}><option value="">off</option>{[0.5, 1, 1.5, 2].map((v) => <option key={v} value={v}>at +{v}R</option>)}</select></Field>
              <Field label="Trailing stop"><select value={edit.tr} onChange={(e) => setEdit({ ...edit, tr: e.target.value })}><option value="">off</option>{[1, 1.5, 2, 3].map((v) => <option key={v} value={v}>{v}× ATR</option>)}</select></Field>
            </div>
            {data.management?.breakeven_done && <p className="small pos">✓ Stop already moved to break-even</p>}
            <button className="btn sm primary" disabled={busy} onClick={() => run(() => api.put(`/positions/${p.id}/management`, { breakeven_r: edit.be ? Number(edit.be) : null, trailing_atr: edit.tr ? Number(edit.tr) : null }), "Saved").then(done).catch(() => {})}>Save</button>
          </Card>
        </div>
      )}
      {open && can("killswitch.use") && <div className="row end"><button className="btn danger sm" onClick={() => window.confirm(`Close ${p.symbol} ${p.side} ${p.volume} now?`) && run(() => api.post(`/positions/${p.id}/close`), "Close requested").then(done)}>Close position now</button></div>}

      <div className="grid two">
        <Card title="Trade journal">
          {note && (
            <>
              <div className="stars" title="Rate how well you followed your plan">
                {[1, 2, 3, 4, 5].map((s) => <span key={s} onClick={() => setNote({ ...note, rating: s })}>{s <= note.rating ? "★" : "☆"}</span>)}
              </div>
              <Field label="What happened / what did you learn?"><textarea rows="3" value={note.note} onChange={(e) => setNote({ ...note, note: e.target.value })} /></Field>
              <Field label="Tags" hint="comma separated, e.g. breakout, news, mistake"><input value={note.tags} onChange={(e) => setNote({ ...note, tags: e.target.value })} /></Field>
              <button className="btn sm" disabled={busy} onClick={() => run(() => api.put(`/positions/${p.id}/note`, { note: note.note, tags: note.tags.split(",").map((x) => x.trim()).filter(Boolean), rating: note.rating || null }), "Journal saved").catch(() => {})}>Save journal</button>
            </>
          )}
          <Screenshots pid={p.id} items={data.attachments || []} onChange={reload} />
          {data.approval && <p className="small" style={{ marginTop: 8 }}><b>Approval:</b> {data.approval.status} by {data.approval.by || "—"} {fmt.dt(data.approval.at)} {data.approval.note && `— “${data.approval.note}”`}</p>}
          {data.signal && (
            <div className="small" style={{ marginTop: 10 }}>
              <b>Why it was opened:</b> {data.signal.strategy} · {data.signal.timeframe}
              <ul>{(data.signal.reasons || []).filter((r) => typeof r !== "object" || r.passed).map((r, i) => <li key={i}>{typeof r === "object" ? [r.side, r.check, r.detail ?? r.value].filter((x) => x !== undefined && x !== null && x !== "").join(" · ") : String(r)}</li>)}</ul>
            </div>
          )}
        </Card>
        <Card title="Timeline">
          <ul className="feed small">
            {data.timeline.map((a, i) => <li key={i}><span className="muted">{fmt.dt(a.at)}</span><b>{ACTION_LABEL[a.action] || a.action}</b><span>{[a.details?.reason, a.details?.status, a.details?.error].filter((x) => x && typeof x !== "object").join(" · ")}</span><span className="muted">{a.user}</span></li>)}
            {data.orders.map((o) => <li key={o.client_id}><span className="muted">{fmt.dt(o.created_at)}</span><b>{o.action}</b><Badge tone={statusTone(o.status)} title={o.error}>{o.status}</Badge><span>{o.fill_price ? fmt.px(o.fill_price, dg) : ""} {o.error}</span></li>)}
          </ul>
        </Card>
      </div>
    </Modal>
  );
}

function NewsStrip({ news, status }) {
  if (!news?.length) return <p className="muted small">{status?.ok ? "No high/medium impact news in the next hours." : `News calendar not loaded yet${status?.error ? ` (${status.error})` : ""}.`}</p>;
  return (
    <ul className="feed small">
      {news.slice(0, 6).map((n, i) => (
        <li key={i}><Badge tone={impactTone(n.impact)}>{n.impact}</Badge><b>{n.currency}</b><span>{n.title}</span><span className="muted">{countdown(n.time)}</span></li>
      ))}
    </ul>
  );
}

function HowTo() {
  return (
    <ol className="howto small">
      <li>Pick the mode on the Dashboard: <b>PRACTICE (PAPER)</b> = virtual money, real prices — start here. <b>BROKER DEMO</b> = your Capital.com demo account.</li>
      <li>Click <b>+ New order</b>, choose the market and BUY (you think price goes up) or SELL (down).</li>
      <li>Keep the suggested stop-loss & take-profit, or type your own prices.</li>
      <li>Set your <b>limit amount</b>: choose <b>Risk $</b> and type how much you accept to lose, e.g. 2 → if the stop-loss hits you lose about $2. It can't be more than your risk limit (shown under the box; change it in Risk & Strategy).</li>
      <li>Read the box that shows "If stop-loss hits / If take-profit hits" — then press Place.</li>
      <li>Click any open position to see its chart, move the stop, add auto-protect and write a journal note.</li>
    </ol>
  );
}

export default function Live() {
  const { can } = useAuth();
  const { data: f, reload } = useApi("/live/feed", { interval: 3000 });
  const { data: strat } = useApi("/system/strategy");
  const [run] = useAction();
  const [ticket, setTicket] = useState(false);
  const [detail, setDetail] = useState(null);
  const [help, setHelp] = useState(false);
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
  const canTrade = ORDER_MODES.includes(f.mode) && f.controls?.manual_trading !== false && !f.kill_switch;
  return (
    <div className="stack">
      <div className="row between wrap gap">
        <div className="row gap wrap">
          <Badge tone={modeTone(f.mode)}>Mode: {modeLabel(f.mode)}</Badge>
          <Badge tone="info">Broker: {f.mode === "PAPER" ? "none (practice)" : f.broker_active}</Badge>
          {f.controls?.auto_trading === false && <Badge tone="warn">AUTO OFF</Badge>}
          {f.kill_switch && <Badge tone="bad">KILL SWITCH</Badge>}
          {f.locked && <Badge tone="bad" title={f.lock_reason}>LOCKED</Badge>}
          <span className="muted small">live · updates every 3 s</span>
        </div>
        <div className="row gap">
          <button className="btn ghost sm" onClick={() => setHelp(!help)}>{help ? "Hide" : "How do I place a trade?"}</button>
          {can("trading.manual") && (
            <button className="btn primary" disabled={!canTrade} title={canTrade ? "" : "Set mode to PRACTICE or BROKER DEMO, switch manual trading on, release the kill switch"} onClick={() => setTicket(true)}>+ New order</button>
          )}
        </div>
      </div>
      {(help || (f.positions.length === 0 && f.pending.length === 0 && f.trades_today === 0)) && <Card title="How to place a trade (2 minutes)"><HowTo /></Card>}
      {!ORDER_MODES.includes(f.mode) && <div className="alert warn small">Trading mode is <b>{modeLabel(f.mode)}</b>, so no orders can be placed. Go to the <a href="#/dashboard">Dashboard</a> and pick <b>PRACTICE (PAPER)</b> to practise with virtual money, or <b>BROKER DEMO</b> for your broker's demo account.</div>}

      <div className="grid stats">
        <Stat label={`Equity · ${modeLabel(f.account_mode)}`} value={fmt.money(a?.equity, a?.currency)} sub={<Sparkline points={spark} />} />
        <Stat label="Open P&L" value={<span className={pnlClass(f.unrealized)}>{fmt.money(f.unrealized)}</span>} sub={`${f.positions.length} open · ${f.pending.length} pending`} />
        <Stat label="Closed today" value={<span className={pnlClass(f.realized_today)}>{fmt.money(f.realized_today)}</span>} sub={`${f.trades_today} trade(s) today`} />
        <Stat label="Balance / free margin" value={fmt.money(a?.balance)} sub={`free ${fmt.money(a?.free_margin)}`} />
      </div>

      <Card title="Open positions — click a row for chart, edit & journal">
        <Table rows={f.positions} empty="No open positions" onRow={(p) => setDetail(p.id)} cols={[
          { label: "Symbol", k: "symbol" },
          { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
          { label: "Lots", k: "volume", num: true },
          { label: "Open", num: true, render: (p) => fmt.px(p.open_price, digitsFor(p.symbol)) },
          { label: "Now", num: true, render: (p) => fmt.px(p.current_price, digitsFor(p.symbol)) },
          { label: "SL", num: true, render: (p) => fmt.px(p.stop_loss, digitsFor(p.symbol)) },
          { label: "TP", num: true, render: (p) => fmt.px(p.take_profit, digitsFor(p.symbol)) },
          { label: "P&L", num: true, render: (p) => <b className={pnlClass(p.unrealized_pnl)}>{fmt.money(p.unrealized_pnl)}</b> },
          { label: "Protect", render: (p) => p.management ? <span className="small">{p.management.breakeven_r ? (p.management.breakeven_done ? "BE ✓" : `BE ${p.management.breakeven_r}R`) : ""} {p.management.trailing_atr ? `trail ${p.management.trailing_atr}×` : ""}</span> : <span className="muted small">—</span> },
          { label: "", render: (p) => can("killswitch.use") && <button className="btn sm" onClick={(e) => { e.stopPropagation(); if (window.confirm(`Close ${p.symbol} ${p.side} ${p.volume}?`)) run(() => api.post(`/positions/${p.id}/close`), "Close requested").then(reload); }}>Close</button> },
        ]} />
      </Card>

      {f.pending.length > 0 && (
        <Card title="Pending orders (waiting for their price)">
          <Table rows={f.pending} cols={[
            { label: "Placed", render: (o) => fmt.dt(o.created_at) },
            { label: "Order", render: (o) => <span><Badge tone={statusTone(o.side)}>{o.side}</Badge> {o.order_type} {o.symbol}</span> },
            { label: "Trigger", num: true, render: (o) => fmt.px(o.trigger_price, digitsFor(o.symbol)) },
            { label: "SL / TP", render: (o) => `${fmt.px(o.stop_loss, digitsFor(o.symbol))} / ${fmt.px(o.take_profit, digitsFor(o.symbol))}` },
            { label: "Size", render: (o) => (o.sizing_mode === "auto" ? "auto" : `${o.sizing_value} ${({ risk_pct: "%", risk_amount: "$ risk", lots: "lots" })[o.sizing_mode]}`) },
            { label: "Expires", render: (o) => countdown(o.expires_at) },
            { label: "", render: (o) => can("trading.manual") && <button className="btn sm" onClick={() => run(() => api.post(`/live/pending/${o.id}/cancel`), "Cancelled").then(reload)}>Cancel</button> },
          ]} />
        </Card>
      )}

      <ControlBar f={f} symbols={symbols} reload={reload} />

      {f.external_positions?.length > 0 && (
        <Card title="Other positions on your broker account (opened outside the app — read only)">
          <Table rows={f.external_positions} rowKey="ticket" cols={[
            { label: "Ticket", k: "ticket" }, { label: "Symbol", k: "symbol" },
            { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
            { label: "Size", k: "size", num: true }, { label: "Open", k: "open_price", num: true },
            { label: "SL", render: (p) => p.sl || <span className="neg">none</span> }, { label: "TP", render: (p) => p.tp || "—" },
            { label: "P&L", num: true, render: (p) => <span className={pnlClass(p.pnl)}>{fmt.money(p.pnl)}</span> },
          ]} />
          <p className="muted small">The app does not manage or close these, and they don't count towards its risk limits.</p>
        </Card>
      )}

      <div className="grid two">
        <Card title="Upcoming market-moving news" actions={<a href="#/news" className="small">Full calendar →</a>}>
          <NewsStrip news={f.news} status={f.news_status} />
        </Card>
        <Card title="Activity">
          <ul className="feed">
            {f.activity.length === 0 && <li className="muted">Nothing yet</li>}
            {f.activity.slice(0, 12).map((x, i) => (
              <li key={i}>
                <span className="muted small">{fmt.dt(x.at)}</span>
                <b>{ACTION_LABEL[x.action] || x.action}</b>
                <span className="small">{[x.details?.symbol, x.details?.side, x.details?.order_type, x.details?.volume && `${x.details.volume} lots`, x.details?.pnl !== undefined && x.details?.pnl !== null && `P&L ${fmt.money(x.details.pnl)}`, typeof x.details?.reason === "string" && x.details.reason, x.details?.to && typeof x.details.to !== "object" && `→ ${x.details.to}`, x.details?.error].filter(Boolean).join(" · ")}</span>
                <span className="muted small">{x.user}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title="Recent orders & system events">
        <Table rows={f.orders.slice(0, 8)} rowKey="client_id" cols={[
          { label: "Time", render: (o) => fmt.dt(o.created_at) }, { label: "Order", render: (o) => `${o.action} ${o.side} ${o.volume} ${o.symbol}` },
          { label: "Mode", render: (o) => modeLabel(o.mode) },
          { label: "Status", render: (o) => <Badge tone={statusTone(o.status)} title={o.error}>{o.status}</Badge> },
          { label: "Fill", num: true, render: (o) => fmt.px(o.fill_price, digitsFor(o.symbol)) },
          { label: "Note", render: (o) => <span className="small muted trunc">{o.error}</span> },
        ]} />
        <ul className="feed small">{f.events.slice(0, 6).map((e, i) => <li key={i}><span className="muted">{fmt.dt(e.at)}</span><Badge tone={{ ERROR: "bad", WARNING: "warn" }[e.level] || "muted"}>{e.level}</Badge><span>{e.message}</span></li>)}</ul>
      </Card>
      {ticket && <OrderTicket symbols={symbols.filter((s) => !(f.controls?.disabled_symbols || []).includes(s))} mode={f.mode} onClose={() => setTicket(false)} onPlaced={() => reload()} />}
      {detail && <PositionDetail id={detail} onClose={() => setDetail(null)} onChanged={reload} />}
    </div>
  );
}
