import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";

// ------------------------------------------------------------------ format
export const fmt = {
  money: (v, ccy = "") => (v === null || v === undefined ? "—" : `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${ccy ? " " + ccy : ""}`),
  num: (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d)),
  px: (v, digits = 5) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits)),
  pct: (v, d = 2) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(d)}%`),
  dt: (v) => (v ? new Date(typeof v === "number" ? v * 1000 : v).toLocaleString(undefined, { year: "2-digit", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"),
  ago: (s) => (s === null || s === undefined ? "never" : s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`),
};
export const digitsFor = (sym) => (sym && sym.endsWith("JPY") ? 3 : 5);
export const pnlClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

// ------------------------------------------------------------------- toast
const ToastCtx = createContext(() => {});
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((msg, kind = "ok") => {
    const id = Math.random();
    setItems((x) => [...x, { id, msg, kind }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), kind === "err" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ------------------------------------------------------------------- hooks
export function useApi(path, { interval = 0, deps = [] } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api.get(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path, ...deps]);
  useEffect(() => {
    alive.current = true;
    load();
    let t;
    if (interval) t = setInterval(() => document.visibilityState === "visible" && load(), interval);
    return () => {
      alive.current = false;
      clearInterval(t);
    };
  }, [load, interval]);
  return { data, error, loading, reload: load, setData };
}

export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (fn, okMsg) => {
      setBusy(true);
      try {
        const r = await fn();
        if (okMsg) toast(okMsg);
        return r;
      } catch (e) {
        toast(e.message, "err");
        throw e;
      } finally {
        setBusy(false);
      }
    },
    [toast]
  );
  return [run, busy];
}

// -------------------------------------------------------------- primitives
export const Card = ({ title, actions, children, className = "" }) => (
  <section className={`card ${className}`}>
    {(title || actions) && (
      <header className="card-h">
        <h3>{title}</h3>
        <div className="row gap">{actions}</div>
      </header>
    )}
    <div className="card-b">{children}</div>
  </section>
);

export const Stat = ({ label, value, sub, tone }) => (
  <div className={`stat ${tone || ""}`}>
    <div className="stat-l">{label}</div>
    <div className="stat-v">{value}</div>
    {sub && <div className="stat-s">{sub}</div>}
  </div>
);

export const Badge = ({ tone = "muted", children, title }) => (
  <span className={`badge ${tone}`} title={title}>{children}</span>
);

export const statusTone = (s) =>
  ({ FILLED: "ok", ORDERED: "ok", OPEN: "info", PENDING: "warn", SENT: "warn", SIGNAL_ONLY: "info", REJECTED: "bad", EXPIRED: "muted", CANCELLED: "muted", NO_TRADE: "muted", CLOSED: "muted", BUY: "ok", SELL: "bad", DONE: "ok", RUNNING: "warn", FAILED: "bad" }[s] || "muted");

export const modeTone = (m) => ({ OFF: "muted", SIGNALS_ONLY: "info", PAPER: "info", MT5_DEMO: "warn", MT5_LIVE: "bad" }[m] || "muted");

export function Table({ cols, rows, empty = "Nothing here yet", onRow, rowKey = "id" }) {
  if (!rows) return <div className="muted pad">Loading…</div>;
  if (!rows.length) return <div className="muted pad">{empty}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{cols.map((c) => <th key={c.k || c.label} className={c.num ? "num" : ""}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r[rowKey] ?? i} onClick={onRow ? () => onRow(r) : undefined} className={onRow ? "click" : ""}>
              {cols.map((c) => (
                <td key={c.k || c.label} className={c.num ? "num" : ""}>{c.render ? c.render(r) : r[c.k] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-label={title}>
        <header className="card-h">
          <h3>{title}</h3>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="card-b">{children}</div>
      </div>
    </div>
  );
}

export const Field = ({ label, hint, children }) => (
  <label className="field">
    <span className="field-l">{label}</span>
    {children}
    {hint && <span className="field-h">{hint}</span>}
  </label>
);

export function PasswordPrompt({ title, message, onSubmit, onClose, extra }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={title} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSubmit(pw);
            onClose();
          } catch {
            /* toast shown by caller */
          } finally {
            setBusy(false);
          }
        }}
      >
        {message && <p className="muted">{message}</p>}
        {extra}
        <Field label="Your password">
          <input type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" />
        </Field>
        <div className="row end gap">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !pw}>Confirm</button>
        </div>
      </form>
    </Modal>
  );
}

export const Meter = ({ value, max, label }) => {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const tone = pct >= 100 ? "bad" : pct >= 60 ? "warn" : "ok";
  return (
    <div className="meter">
      <div className="row between small">
        <span>{label}</span>
        <span>{fmt.money(Math.max(0, value))} / {fmt.money(max)}</span>
      </div>
      <div className="meter-t"><div className={`meter-f ${tone}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
};

export const Tabs = ({ tabs, value, onChange }) => (
  <div className="tabs" role="tablist">
    {tabs.map((t) => (
      <button key={t.k} role="tab" aria-selected={value === t.k} className={value === t.k ? "tab on" : "tab"} onClick={() => onChange(t.k)}>
        {t.label}
      </button>
    ))}
  </div>
);

export const JsonView = ({ data }) => <pre className="json">{JSON.stringify(data, null, 2)}</pre>;

// ------------------------------------------------------------------ charts
function useWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(300, e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const niceTicks = (min, max, n = 5) => {
  if (!(max > min)) return [min];
  const step = Math.pow(10, Math.floor(Math.log10((max - min) / n)));
  const err = ((max - min) / n) / step;
  const s = step * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1);
  const out = [];
  for (let v = Math.ceil(min / s) * s; v <= max + 1e-12; v += s) out.push(v);
  return out;
};

export function LineChart({ series, height = 220, yFormat = (v) => fmt.num(v, 2), baseline }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState(null);
  const pts = series[0]?.points || [];
  if (!pts.length) return <div ref={ref} className="muted pad">No data yet</div>;
  const pad = { l: 64, r: 12, t: 10, b: 24 };
  const all = series.flatMap((s) => s.points.map((p) => p.y)).concat(baseline !== undefined ? [baseline] : []);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const xs = pts.map((p) => p.x);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const X = (x) => pad.l + ((x - x0) / Math.max(1, x1 - x0)) * (w - pad.l - pad.r);
  const Y = (y) => pad.t + (1 - (y - lo) / (hi - lo)) * (height - pad.t - pad.b);
  const ticks = niceTicks(lo, hi, 4);
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={height} onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const xv = x0 + ((e.clientX - r.left - pad.l) / (w - pad.l - pad.r)) * (x1 - x0);
          let best = 0;
          pts.forEach((p, i) => { if (Math.abs(p.x - xv) < Math.abs(pts[best].x - xv)) best = i; });
          setHover(best);
        }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={Y(t)} y2={Y(t)} className="grid" />
            <text x={pad.l - 6} y={Y(t) + 4} textAnchor="end" className="axis">{yFormat(t)}</text>
          </g>
        ))}
        {baseline !== undefined && <line x1={pad.l} x2={w - pad.r} y1={Y(baseline)} y2={Y(baseline)} className="baseline" />}
        {series.map((s, si) => (
          <path key={si} d={s.points.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("")} fill="none" stroke={s.color || "var(--accent)"} strokeWidth="1.8" />
        ))}
        {hover !== null && (
          <g>
            <line x1={X(pts[hover].x)} x2={X(pts[hover].x)} y1={pad.t} y2={height - pad.b} className="cross" />
            <circle cx={X(pts[hover].x)} cy={Y(pts[hover].y)} r="3.5" fill="var(--accent)" />
            <text x={Math.min(X(pts[hover].x) + 6, w - 150)} y={pad.t + 12} className="tip">
              {fmt.dt(pts[hover].x)} · {yFormat(pts[hover].y)}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

export function CandleChart({ candles, digits = 5, markers = [] }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState(null);
  const H = { main: 300, rsi: 80, macd: 90, gap: 8 };
  const total = H.main + H.rsi + H.macd + H.gap * 2 + 22;
  const pad = { l: 8, r: 70 };
  const data = useMemo(() => candles || [], [candles]);
  if (!data.length) return <div ref={ref} className="muted pad">No candles stored for this symbol/timeframe yet.</div>;
  const n = data.length;
  const cw = (w - pad.l - pad.r) / n;
  const X = (i) => pad.l + i * cw + cw / 2;
  const prices = data.flatMap((c) => [c.high, c.low, c.ema_fast, c.ema_slow].filter((v) => v !== null && v !== undefined));
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const Ym = (v) => 6 + (1 - (v - lo) / (hi - lo || 1)) * (H.main - 12);
  const rsiTop = H.main + H.gap;
  const Yr = (v) => rsiTop + (1 - v / 100) * H.rsi;
  const macdTop = rsiTop + H.rsi + H.gap;
  const mv = data.flatMap((c) => [c.macd, c.macd_signal, c.macd_hist].filter((v) => v !== null && v !== undefined));
  const mabs = Math.max(1e-9, ...mv.map(Math.abs));
  const Yd = (v) => macdTop + H.macd / 2 - (v / mabs) * (H.macd / 2 - 4);
  const line = (key, Y) => {
    let d = "", started = false;
    data.forEach((c, i) => {
      const v = c[key];
      if (v === null || v === undefined) { started = false; return; }
      d += `${started ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
      started = true;
    });
    return d;
  };
  const h = hover !== null ? data[hover] : data[n - 1];
  const markIdx = new Map(markers.map((m) => [m.time, m]));
  return (
    <div ref={ref} className="chart">
      <div className="chart-legend small">
        <span>{fmt.dt(h.time)}</span>
        <span>O {fmt.px(h.open, digits)} H {fmt.px(h.high, digits)} L {fmt.px(h.low, digits)} C {fmt.px(h.close, digits)}</span>
        <span className="c-fast">EMA fast {fmt.px(h.ema_fast, digits)}</span>
        <span className="c-slow">EMA slow {fmt.px(h.ema_slow, digits)}</span>
        <span>RSI {fmt.num(h.rsi, 1)}</span>
        <span>MACD hist {h.macd_hist !== null ? Number(h.macd_hist).toExponential(2) : "—"}</span>
        <span>ATR {fmt.px(h.atr, digits)}</span>
      </div>
      <svg width={w} height={total} onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const i = Math.floor((e.clientX - r.left - pad.l) / cw);
          setHover(i >= 0 && i < n ? i : null);
        }}>
        {niceTicks(lo, hi, 6).map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={Ym(t)} y2={Ym(t)} className="grid" />
            <text x={w - pad.r + 4} y={Ym(t) + 4} className="axis">{fmt.px(t, digits)}</text>
          </g>
        ))}
        {data.map((c, i) => {
          const up = c.close >= c.open;
          const bw = Math.max(1, cw * 0.65);
          return (
            <g key={c.time} className={up ? "up" : "down"}>
              <line x1={X(i)} x2={X(i)} y1={Ym(c.high)} y2={Ym(c.low)} className="wick" />
              <rect x={X(i) - bw / 2} y={Ym(Math.max(c.open, c.close))} width={bw} height={Math.max(1, Math.abs(Ym(c.open) - Ym(c.close)))} />
            </g>
          );
        })}
        <path d={line("ema_fast", Ym)} className="ema-fast" />
        <path d={line("ema_slow", Ym)} className="ema-slow" />
        {data.map((c, i) => {
          const m = markIdx.get(c.time);
          if (!m) return null;
          const buy = m.action === "BUY";
          const y = buy ? Ym(c.low) + 12 : Ym(c.high) - 12;
          return <path key={`m${i}`} d={buy ? `M${X(i)},${y - 8}l5,9h-10z` : `M${X(i)},${y + 8}l5,-9h-10z`} className={buy ? "mk-buy" : "mk-sell"}><title>{m.action} · {m.status}</title></path>;
        })}
        {/* RSI */}
        <rect x={pad.l} y={rsiTop} width={w - pad.l - pad.r} height={H.rsi} className="panel" />
        {[30, 50, 70].map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={w - pad.r} y1={Yr(v)} y2={Yr(v)} className="grid" />
            <text x={w - pad.r + 4} y={Yr(v) + 4} className="axis">{v}</text>
          </g>
        ))}
        <path d={line("rsi", Yr)} className="rsi" />
        <text x={pad.l + 4} y={rsiTop + 12} className="axis">RSI</text>
        {/* MACD */}
        <rect x={pad.l} y={macdTop} width={w - pad.l - pad.r} height={H.macd} className="panel" />
        <line x1={pad.l} x2={w - pad.r} y1={Yd(0)} y2={Yd(0)} className="grid" />
        {data.map((c, i) => c.macd_hist === null ? null : (
          <rect key={`h${i}`} x={X(i) - Math.max(0.5, cw * 0.3)} width={Math.max(1, cw * 0.6)}
            y={Math.min(Yd(0), Yd(c.macd_hist))} height={Math.abs(Yd(c.macd_hist) - Yd(0))} className={c.macd_hist >= 0 ? "hist-up" : "hist-down"} />
        ))}
        <path d={line("macd", Yd)} className="macd" />
        <path d={line("macd_signal", Yd)} className="macd-sig" />
        <text x={pad.l + 4} y={macdTop + 12} className="axis">MACD</text>
        {hover !== null && <line x1={X(hover)} x2={X(hover)} y1={0} y2={macdTop + H.macd} className="cross" />}
        <text x={pad.l} y={total - 4} className="axis">{fmt.dt(data[0].time)}</text>
        <text x={w - pad.r} y={total - 4} textAnchor="end" className="axis">{fmt.dt(data[n - 1].time)}</text>
      </svg>
    </div>
  );
}
