import React, { useMemo, useState } from "react";
import { Badge, Card, Table, digitsFor, fmt, useApi, useWidth } from "../components.jsx";

const KIND = {
  sweep_bsl: "BSL sweep", sweep_ssl: "SSL sweep", bos: "BOS", choch: "CHoCH / MSS", fvg: "Fair value gap",
  swing_high: "Swing high", swing_low: "Swing low", displacement: "Displacement",
};
const toS = (t) => Math.floor(new Date(t).getTime() / 1000);

function SmcChart({ data, show }) {
  const [ref, w] = useWidth();
  const c = data.candles;
  const H = 420, pad = { l: 6, r: 74, t: 10, b: 22 };
  const d = digitsFor(data.symbol);
  const idx = useMemo(() => new Map(c.map((x, i) => [x.time, i])), [c]);
  if (!c.length) return <div ref={ref} />;
  const nearest = (t) => {
    const s = toS(t);
    if (idx.has(s)) return idx.get(s);
    let best = 0;
    c.forEach((x, i) => { if (Math.abs(x.time - s) < Math.abs(c[best].time - s)) best = i; });
    return best;
  };
  const lo = Math.min(...c.map((x) => x.low)), hi = Math.max(...c.map((x) => x.high));
  const m = (hi - lo) * 0.04;
  const cw = (w - pad.l - pad.r) / c.length;
  const X = (i) => pad.l + i * cw + cw / 2;
  const Y = (v) => pad.t + (1 - (v - lo + m) / (hi - lo + 2 * m)) * (H - pad.t - pad.b);
  const ev = data.events;
  const inView = (v) => v >= lo - m && v <= hi + m;
  const uniq = (arr) => [...new Map(arr.map((v) => [v.toFixed(d), v])).values()];
  let lastY = -99;
  const levels = [...uniq(data.state.bsl).filter(inView).map((v) => ({ t: "BSL", v })), ...uniq(data.state.ssl).filter(inView).map((v) => ({ t: "SSL", v }))]
    .sort((a, b) => b.v - a.v).map((L) => { const y = Y(L.v); const label = y - lastY > 12; if (label) lastY = y; return { ...L, label }; });
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H}>
        {show.fvg && data.state.open_fvgs.map((f, k) => {
          const i = nearest(f.time);
          return <rect key={`of${k}`} x={X(i) - cw} y={Y(f.zone_high)} width={Math.max(2, X(c.length - 1) - X(i) + cw)} height={Math.max(1, Y(f.zone_low) - Y(f.zone_high))}
            fill={f.dir === "bull" ? "var(--ok)" : "var(--bad)"} opacity="0.13" />;
        })}
        {c.map((x, i) => {
          const up = x.close >= x.open;
          return (
            <g key={x.time}>
              <line x1={X(i)} x2={X(i)} y1={Y(x.high)} y2={Y(x.low)} stroke={up ? "var(--ok)" : "var(--bad)"} strokeWidth="1" />
              <rect x={X(i) - Math.max(1, cw * 0.35)} width={Math.max(2, cw * 0.7)} y={Y(Math.max(x.open, x.close))} height={Math.max(1, Math.abs(Y(x.open) - Y(x.close)))} fill={up ? "var(--ok)" : "var(--bad)"} />
            </g>
          );
        })}
        {c[0].ema_slow != null && <path d={c.map((x, i) => (x.ema_slow == null ? "" : `${i && c[i - 1].ema_slow != null ? "L" : "M"}${X(i).toFixed(1)},${Y(x.ema_slow).toFixed(1)}`)).join("")} fill="none" stroke="#a78bfa" strokeWidth="1.2" opacity="0.8" />}
        {show.liq && levels.map((L) => <g key={`${L.t}${L.v}`}><line x1={pad.l} x2={w - pad.r} y1={Y(L.v)} y2={Y(L.v)} stroke={L.t === "BSL" ? "var(--warn)" : "var(--info)"} strokeDasharray="6 4" />
          {L.label && <text x={w - pad.r + 3} y={Y(L.v) + 4} className="axis" fill={L.t === "BSL" ? "var(--warn)" : "var(--info)"}>{L.t} {L.v.toFixed(d)}</text>}</g>)}
        {ev.map((e, k) => {
          if ((e.kind === "swing_high" || e.kind === "swing_low") && show.swings) {
            const i = nearest(e.pivot_time);
            const top = e.kind === "swing_high";
            return <circle key={k} cx={X(i)} cy={Y(e.price) + (top ? -6 : 6)} r="2.6" fill={top ? "var(--warn)" : "var(--info)"}><title>{KIND[e.kind]} {e.price.toFixed(d)} (confirmed {fmt.dt(e.time)})</title></circle>;
          }
          if ((e.kind === "bos" || e.kind === "choch") && show.structure) {
            const a = nearest(e.swing_time), b = nearest(e.time);
            const col = e.dir === "bull" ? "var(--ok)" : "var(--bad)";
            return <g key={k}><line x1={X(a)} x2={X(b)} y1={Y(e.level)} y2={Y(e.level)} stroke={col} strokeWidth="1.4" strokeDasharray={e.kind === "choch" ? "3 3" : undefined} />
              <text x={(X(a) + X(b)) / 2} y={Y(e.level) + (e.dir === "bull" ? -4 : 12)} className="axis" fill={col} textAnchor="middle">{e.kind === "bos" ? "BOS" : "CHoCH"}</text></g>;
          }
          if ((e.kind === "sweep_bsl" || e.kind === "sweep_ssl") && show.sweeps) {
            const i = nearest(e.time);
            const y = Y(e.extreme) + (e.kind === "sweep_bsl" ? -9 : 9);
            return <text key={k} x={X(i)} y={y + 4} textAnchor="middle" fontSize="12" fill="var(--warn)"><title>{KIND[e.kind]} of {e.level.toFixed(d)}</title>✕</text>;
          }
          if (e.kind === "fvg" && show.fvg) {
            const i = nearest(e.time);
            return <rect key={k} x={X(i) - cw * 1.5} y={Y(e.zone_high)} width={cw * 3} height={Math.max(1, Y(e.zone_low) - Y(e.zone_high))} fill="none"
              stroke={e.dir === "bull" ? "var(--ok)" : "var(--bad)"} strokeWidth="0.8" opacity="0.7"><title>FVG {e.zone_low.toFixed(d)}–{e.zone_high.toFixed(d)}</title></rect>;
          }
          if (e.kind === "displacement" && show.disp) {
            const i = nearest(e.time);
            return <rect key={k} x={X(i) - cw * 0.5} y={pad.t} width={cw} height={H - pad.t - pad.b} fill="var(--accent)" opacity="0.06" />;
          }
          return null;
        })}
      </svg>
    </div>
  );
}

export default function Smc() {
  const { data: strat } = useApi("/system/strategy");
  const [sym, setSym] = useState("EURUSD");
  const [tf, setTf] = useState("H1");
  const [bars, setBars] = useState(150);
  const [show, setShow] = useState({ swings: true, liq: true, structure: true, sweeps: true, fvg: true, disp: false });
  const { data, error } = useApi(`/smc/scan?symbol=${sym}&timeframe=${tf}&bars=${bars}`, { interval: 60000, deps: [sym, tf, bars] });
  const { data: log } = useApi(`/smc/events?symbol=${sym}&kind=sweep_bsl,sweep_ssl,bos,choch&days=14&limit=40`, { interval: 60000, deps: [sym] });
  const recent = useMemo(() => (data?.events || []).filter((e) => ["sweep_bsl", "sweep_ssl", "bos", "choch", "fvg"].includes(e.kind)).slice(-12).reverse(), [data]);
  const d = digitsFor(sym);
  return (
    <div className="stack">
      <Card title="SMC scanner" actions={
        <>
          <select value={sym} onChange={(e) => setSym(e.target.value)}>{(strat?.symbols || ["EURUSD"]).map((s) => <option key={s}>{s}</option>)}</select>
          <select value={tf} onChange={(e) => setTf(e.target.value)}>{["M15", "H1", "H4"].map((t) => <option key={t}>{t}</option>)}</select>
          <select value={bars} onChange={(e) => setBars(Number(e.target.value))}>{[100, 150, 250, 400].map((b) => <option key={b} value={b}>{b} candles</option>)}</select>
        </>
      }>
        {error && <div className="alert warn small">{error}</div>}
        <div className="chips" style={{ marginBottom: 8 }}>
          {[["swings", "Swing points"], ["liq", "BSL / SSL"], ["structure", "BOS / CHoCH"], ["sweeps", "Sweeps ✕"], ["fvg", "FVG"], ["disp", "Displacement"]].map(([k, l]) => (
            <button key={k} className={`chip ${show[k] ? "on" : ""}`} onClick={() => setShow({ ...show, [k]: !show[k] })}>{l}</button>
          ))}
        </div>
        {data && <SmcChart data={data} show={show} />}
        {data && (
          <div className="grid stats" style={{ marginTop: 10 }}>
            <div className="stat"><div className="stat-l">Structure</div><div className="stat-v"><Badge tone={data.state.trend === "bullish" ? "ok" : data.state.trend === "bearish" ? "bad" : "muted"}>{data.state.trend}</Badge></div></div>
            <div className="stat"><div className="stat-l">Buy-side liquidity (above)</div><div className="small mono">{data.state.bsl.map((v) => v.toFixed(d)).join(" · ") || "—"}</div></div>
            <div className="stat"><div className="stat-l">Sell-side liquidity (below)</div><div className="small mono">{data.state.ssl.map((v) => v.toFixed(d)).join(" · ") || "—"}</div></div>
            <div className="stat"><div className="stat-l">Open fair value gaps</div><div className="small mono">{data.state.open_fvgs.map((f) => `${f.dir === "bull" ? "▲" : "▼"} ${f.zone_low.toFixed(d)}–${f.zone_high.toFixed(d)}`).join(" · ") || "—"}</div></div>
          </div>
        )}
      </Card>
      <div className="grid two">
        <Card title="Latest detections on this chart">
          <Table rows={recent.map((e, i) => ({ ...e, id: i }))} empty="No sweeps, breaks or FVGs in view" cols={[
            { label: "Candle", render: (e) => fmt.dt(e.time) },
            { label: "Event", render: (e) => <Badge tone={e.dir === "bull" ? "ok" : "bad"}>{KIND[e.kind]}</Badge> },
            { label: "Level / zone", render: (e) => <span className="mono small">{e.kind === "fvg" ? `${e.zone_low.toFixed(d)}–${e.zone_high.toFixed(d)}` : (e.level ?? e.price)?.toFixed(d)}</span> },
          ]} />
        </Card>
        <Card title="Event log (all timeframes, 14 days)" actions={<a className="btn sm" href={`/api/smc/events?symbol=${sym}&days=90&limit=5000&fmt=csv`}>⬇ CSV</a>}>
          <Table rows={(log || []).map((e, i) => ({ ...e, id: i }))} empty="Nothing logged yet — the engine logs detections as candles close" cols={[
            { label: "Candle", render: (e) => fmt.dt(e.time) }, { label: "TF", k: "timeframe" },
            { label: "Event", render: (e) => <Badge tone={e.dir === "bull" ? "ok" : "bad"}>{KIND[e.kind]}</Badge> },
            { label: "Level", render: (e) => <span className="mono small">{e.level?.toFixed(d)}</span> },
          ]} />
        </Card>
      </div>
      <Card title="How these are measured (the app's written rules)">
        <ul className="small">
          <li><b>Swing high / low</b>: a candle whose high (low) is beyond the {strat?.smc_swing_n ?? 3} candles on each side. It only counts after those {strat?.smc_swing_n ?? 3} later candles have closed — no peeking into the future.</li>
          <li><b>BSL / SSL</b>: unbroken swing highs (buy-side liquidity, orange) and swing lows (sell-side liquidity, blue) where stop orders may cluster.</li>
          <li><b>Sweep ✕</b>: price trades beyond a swing level but the candle closes back inside. A chart cannot prove deliberate "stop hunting".</li>
          <li><b>BOS / CHoCH</b>: a candle <i>closes</i> beyond the latest swing level — BOS if with the current structure, CHoCH (dashed) if it flips it.</li>
          <li><b>Displacement</b>: a candle body ≥ {strat?.smc_displacement_atr ?? 1.2} × ATR that is at least half of its range.</li>
          <li><b>FVG</b>: a three-candle gap (low of candle 3 above high of candle 1, or the mirror). Shaded = still open. A revisit is possible, not guaranteed.</li>
          <li><b>Order block</b>: the last opposite candle (within 10) before the break. It is a zone by definition, not proof of institutional orders.</li>
        </ul>
      </Card>
    </div>
  );
}
