import React, { useEffect, useMemo, useRef, useState } from "react";
import { useWidth } from "../components.jsx";

/* Candle chart: zoom (wheel / pinch / buttons), pan (drag / swipe), crosshair, live follow,
   optional indicators (EMA, Bollinger, volume, RSI, MACD), strategy markers and user drawings (lines / trend lines). */
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function timeLabel(sec, tfSec) {
  const d = new Date(sec * 1000);
  if (tfSec >= 86400) return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  if (tfSec >= 3600) return `${d.toLocaleDateString(undefined, { day: "2-digit", month: "short" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
  if (tfSec >= 60) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const DEFAULT_IND = { ema: true, bb: false, volume: false, rsi: false, macd: false, markers: true };

export default function ZoomChart({ candles = [], digits = 5, levels = [], tfSec = 3600, height, ind = DEFAULT_IND, markers = [],
  drawings = [], tool = null, onCreate, selected, onSelect, compact = false }) {
  const [ref, w] = useWidth();
  const mobile = w < 600;
  const touch = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches;
  const H = height || (compact ? 240 : mobile ? 280 : 380);
  const SUB = compact ? 60 : 84;
  const panes = [ind.rsi && "rsi", ind.macd && "macd"].filter(Boolean);
  const totalH = H + panes.length * (SUB + 6);
  const pad = { l: 6, r: mobile ? 58 : 72, t: 10, b: 22 };
  const n = candles.length;
  const defCount = compact ? 60 : mobile ? 50 : 90;
  const [view, setView] = useState({ count: defCount, end: 0, follow: true });
  const [hov, setHov] = useState(null);
  const [pending, setPending] = useState(null); // first point of a trend line
  const drag = useRef(null);
  const svgRef = useRef(null);
  useEffect(() => { setPending(null); }, [tool]);

  const end = view.follow ? n : clamp(view.end, Math.min(n, 5), n);
  const count = clamp(view.count, 10, Math.max(10, n));
  const start = Math.max(0, end - count);
  const vis = candles.slice(start, end);
  const plotW = w - pad.l - pad.r;
  const cw = plotW / Math.max(count, 1);

  const { lo, hi } = useMemo(() => {
    const vals = vis.flatMap((x) => [x.high, x.low]);
    if (ind.bb) vis.forEach((x) => { if (x.bb_up != null) vals.push(x.bb_up, x.bb_lo); });
    if (!vals.length) return { lo: 0, hi: 1 };
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const extra = levels.map((l) => l.price).concat(drawings.filter((d) => d.kind === "hline").map((d) => d.data.price))
      .filter((p) => p && p > mn - (mx - mn) * 0.5 && p < mx + (mx - mn) * 0.5);
    const a = Math.min(mn, ...extra), b = Math.max(mx, ...extra);
    const m = (b - a) * 0.06 || a * 0.001 || 0.001;
    return { lo: a - m, hi: b + m };
  }, [vis, levels, drawings, ind.bb]);

  const setZoom = (factor, anchorFrac = 1) => setView((v) => {
    const curEnd = v.follow ? n : clamp(v.end, 5, n);
    const curCount = clamp(v.count, 10, Math.max(10, n));
    const nc = clamp(Math.round(curCount * factor), 10, Math.max(10, n));
    const anchorIdx = curEnd - curCount + anchorFrac * curCount;
    const ne = clamp(Math.round(anchorIdx + (1 - anchorFrac) * nc), Math.min(n, nc), n);
    return { count: nc, end: ne, follow: ne >= n };
  });
  const panBy = (bars) => setView((v) => {
    const curEnd = v.follow ? n : v.end;
    const ne = clamp(Math.round(curEnd - bars), Math.min(n, 10), n);
    return { ...v, end: ne, follow: ne >= n };
  });

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const frac = clamp((e.clientX - r.left - pad.l) / plotW, 0, 1);
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) panBy(-e.deltaX / Math.max(cw, 1) * 0.5);
      else setZoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, frac);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  if (!n) return <div ref={ref} className="muted pad">No price history for this timeframe yet.</div>;

  const X = (i) => pad.l + (i - start) * cw + cw / 2;
  const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const P = (y) => lo + (1 - (y - pad.t) / (H - pad.t - pad.b)) * (hi - lo);
  // time <-> fractional candle index (handles gaps such as weekends)
  const idxOf = (t) => {
    if (t <= candles[0].time) return (t - candles[0].time) / tfSec;
    if (t >= candles[n - 1].time) return n - 1 + (t - candles[n - 1].time) / tfSec;
    let a = 0, b = n - 1;
    while (b - a > 1) { const m = (a + b) >> 1; if (candles[m].time <= t) a = m; else b = m; }
    return a + (t - candles[a].time) / Math.max(1, candles[b].time - candles[a].time);
  };
  const timeOf = (i) => {
    const k = Math.floor(clamp(i, 0, n - 1));
    return candles[k].time + (i - k) * tfSec;
  };
  const ticks = [...Array(6).keys()].map((k) => lo + ((hi - lo) * (k + 0.5)) / 6);
  const line = (k, yf = Y) => vis.map((x, j) => (x[k] == null ? "" : `${j && vis[j - 1][k] != null ? "L" : "M"}${X(start + j).toFixed(1)},${yf(x[k]).toFixed(1)}`)).join("");
  const step = Math.max(1, Math.ceil(count / Math.max(2, Math.floor(plotW / (tfSec >= 3600 && tfSec < 86400 ? 110 : 80)))));
  const hv = hov && hov.i >= start && hov.i < end ? candles[hov.i] : null;
  const lastC = candles[n - 1];
  const vmax = ind.volume ? Math.max(1, ...vis.map((x) => x.volume || 0)) : 1;
  const visStart = candles[start]?.time, visEnd = candles[end - 1]?.time;

  const pt = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    const t = e.touches ? e.touches[0] : e.changedTouches ? e.changedTouches[0] : e;
    return { x: t.clientX - r.left, y: t.clientY - r.top };
  };
  const onDown = (e) => {
    if (e.touches && e.touches.length === 2) {
      drag.current = { pinch: Math.abs(e.touches[0].clientX - e.touches[1].clientX), count };
      return;
    }
    drag.current = { x: pt(e).x, end, moved: false };
  };
  const onMove = (e) => {
    if (drag.current?.pinch && e.touches?.length === 2) {
      const d = Math.abs(e.touches[0].clientX - e.touches[1].clientX) || 1;
      setView((v) => ({ ...v, count: clamp(Math.round(drag.current.count * (drag.current.pinch / d)), 10, Math.max(10, n)) }));
      return;
    }
    const p = pt(e);
    if (drag.current && drag.current.x != null && !tool) {
      const dx = p.x - drag.current.x;
      if (Math.abs(dx) > 3) drag.current.moved = true;
      const ne = clamp(Math.round(drag.current.end - dx / cw), Math.min(n, 10), n);
      setView((v) => ({ ...v, end: ne, follow: ne >= n }));
      if (e.touches) return;
    }
    const fi = (p.x - pad.l) / cw + start - 0.5;
    setHov({ i: clamp(Math.round(fi), start, end - 1), fi, y: p.y, x: p.x });
  };
  const onUp = (e) => {
    const moved = drag.current?.moved;
    drag.current = null;
    if (moved || !tool || !onCreate) return;
    const p = pt(e);
    if (p.y < pad.t || p.y > H - pad.b) return;
    const price = +P(p.y).toFixed(digits);
    const fi = (p.x - pad.l) / cw + start - 0.5;
    if (tool === "hline" || tool === "alert") onCreate({ kind: "hline", data: { price }, alert: tool === "alert" });
    else if (tool === "trend") {
      const t = Math.round(timeOf(fi));
      if (!pending) setPending({ t, p: price });
      else { onCreate({ kind: "trend", data: { t1: pending.t, p1: pending.p, t2: t, p2: price } }); setPending(null); }
    }
  };

  const subY = (k, v, mn, mx) => H + 6 + k * (SUB + 6) + 4 + (1 - (v - mn) / (mx - mn || 1)) * (SUB - 8);
  const macdVals = ind.macd ? vis.flatMap((x) => [x.macd, x.macd_signal, x.macd_hist]).filter((v) => v != null) : [];
  const mAbs = macdVals.length ? Math.max(...macdVals.map(Math.abs)) : 1;

  return (
    <div ref={ref} className="chart zchart">
      <div className="zbar">
        {hv ? (
          <span className="mono tiny">{timeLabel(hv.time, tfSec)} · O {hv.open.toFixed(digits)} H {hv.high.toFixed(digits)} L {hv.low.toFixed(digits)} C <b className={hv.close >= hv.open ? "pos" : "neg"}>{hv.close.toFixed(digits)}</b>
            {ind.rsi && hv.rsi != null && <> · RSI {hv.rsi.toFixed(0)}</>}{ind.volume && hv.volume != null && <> · Vol {Math.round(hv.volume)}</>}</span>
        ) : <span className="tiny muted">{tool ? (tool === "trend" ? (pending ? "Tap the second point" : "Tap the first point of the trend line") : "Tap the chart at the price for the line") : touch ? "Swipe to move · pinch to zoom" : "Scroll to zoom · drag to move · double-click to reset"}</span>}
        <span className="zbtns">
          <button title="Zoom out" onClick={() => setZoom(1.4)}>−</button>
          <button title="Zoom in" onClick={() => setZoom(1 / 1.4)}>+</button>
          <button title="Back to latest" className={view.follow ? "" : "on"} onClick={() => setView({ count: defCount, end: n, follow: true })}>⇥{compact ? "" : " Latest"}</button>
        </span>
      </div>
      <svg ref={svgRef} width={w} height={totalH} style={{ touchAction: "none", cursor: tool ? "copy" : drag.current ? "grabbing" : "crosshair" }}
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => { drag.current = null; setHov(null); }}
        onTouchStart={onDown} onTouchMove={onMove} onTouchEnd={onUp}
        onDoubleClick={() => setView({ count: defCount, end: n, follow: true })}>
        {ticks.map((v) => <g key={v}><line x1={pad.l} x2={w - pad.r} y1={Y(v)} y2={Y(v)} className="grid" /><text x={w - pad.r + 4} y={Y(v) + 4} className="axis">{v.toFixed(digits)}</text></g>)}
        {ind.volume && vis.map((x, j) => x.volume ? (
          <rect key={`v${x.time}`} x={X(start + j) - Math.max(0.8, cw * 0.34)} width={Math.max(1.6, cw * 0.68)} y={H - pad.b - (x.volume / vmax) * (H - pad.t - pad.b) * 0.18}
            height={(x.volume / vmax) * (H - pad.t - pad.b) * 0.18} fill={x.close >= x.open ? "rgba(34,197,94,.25)" : "rgba(239,68,68,.25)"} />) : null)}
        {ind.bb && <>
          <path d={line("bb_up")} fill="none" stroke="#94a3b8" strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
          <path d={line("bb_lo")} fill="none" stroke="#94a3b8" strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
        </>}
        {vis.map((x, j) => {
          const i = start + j;
          const up = x.close >= x.open;
          return (
            <g key={x.time} className={up ? "cu" : "cd"}>
              <line x1={X(i)} x2={X(i)} y1={Y(x.high)} y2={Y(x.low)} strokeWidth="1.1" />
              <rect x={X(i) - Math.max(0.8, cw * 0.34)} width={Math.max(1.6, cw * 0.68)} y={Y(Math.max(x.open, x.close))} height={Math.max(1, Math.abs(Y(x.open) - Y(x.close)))} rx="1" />
            </g>
          );
        })}
        {vis.map((x, j) => ((start + j) % step === 0 ? <text key={`t${x.time}`} x={X(start + j)} y={H - 6} className="axis" textAnchor="middle">{timeLabel(x.time, tfSec)}</text> : null))}
        {ind.ema && <>
          <path d={line("ema20")} fill="none" stroke="#38bdf8" strokeWidth="1.5" />
          <path d={line("ema50")} fill="none" stroke="#a78bfa" strokeWidth="1.2" opacity="0.7" />
        </>}
        {ind.markers && markers.filter((m) => m.time >= visStart && m.time <= visEnd).map((m) => {
          const i = Math.round(idxOf(m.time));
          const c = candles[i];
          if (!c) return null;
          const x = X(i), buy = m.action === "BUY";
          const y = buy ? Y(c.low) + 12 : Y(c.high) - 12;
          return <path key={`m${m.id}`} d={buy ? `M${x},${y - 6} l5,8 h-10 z` : `M${x},${y + 6} l5,-8 h-10 z`}
            fill={buy ? "var(--ok)" : "var(--bad)"} stroke="#fff" strokeWidth="0.6"><title>{`${m.action} · ${m.strategy} · ${m.status}`}</title></path>;
        })}
        {levels.filter((l) => l.price && l.price > lo && l.price < hi).map((l, k) => (
          <g key={k}><line x1={pad.l} x2={w - pad.r} y1={Y(l.price)} y2={Y(l.price)} stroke={l.color} strokeDasharray="5 4" />
            <text x={w - pad.r + 4} y={Y(l.price) - 3} className="axis" fill={l.color}>{l.label}</text></g>
        ))}
        {drawings.map((d) => {
          const sel = selected === d.id;
          const col = d.color || "#f2b443";
          if (d.kind === "hline") {
            const p = d.data.price;
            if (!(p > lo && p < hi)) return null;
            return (
              <g key={`d${d.id}`} className="drawing" onClick={(e) => { e.stopPropagation(); onSelect?.(d.id); }}>
                <line x1={pad.l} x2={w - pad.r} y1={Y(p)} y2={Y(p)} stroke="transparent" strokeWidth="10" />
                <line x1={pad.l} x2={w - pad.r} y1={Y(p)} y2={Y(p)} stroke={col} strokeWidth={sel ? 2.4 : 1.4} />
                <rect x={w - pad.r} y={Y(p) - 9} width={pad.r - 2} height="18" rx="3" fill={col} />
                <text x={w - pad.r + 4} y={Y(p) + 4} fill="#06121b" fontSize="11" className="mono">{d.alert ? "🔔" : ""}{p.toFixed(digits)}</text>
                {d.label && <text x={pad.l + 4} y={Y(p) - 4} className="axis" fill={col}>{d.label}</text>}
              </g>
            );
          }
          const i1 = idxOf(d.data.t1), i2 = idxOf(d.data.t2);
          if (i1 === i2) return null;
          const slope = (d.data.p2 - d.data.p1) / (i2 - i1);
          const xe = end - 1 + 0.5; // extend to the right edge
          const pe = d.data.p2 + slope * (xe - i2);
          return (
            <g key={`d${d.id}`} className="drawing" onClick={(e) => { e.stopPropagation(); onSelect?.(d.id); }}>
              <line x1={X(i1)} y1={Y(d.data.p1)} x2={X(xe)} y2={Y(pe)} stroke="transparent" strokeWidth="10" />
              <line x1={X(i1)} y1={Y(d.data.p1)} x2={X(xe)} y2={Y(pe)} stroke={col} strokeWidth={sel ? 2.4 : 1.4} />
              <circle cx={X(i1)} cy={Y(d.data.p1)} r="3" fill={col} /><circle cx={X(i2)} cy={Y(d.data.p2)} r="3" fill={col} />
              {d.alert && <text x={X(xe) - 14} y={Y(pe) - 6} fontSize="11">🔔</text>}
            </g>
          );
        })}
        {pending && <circle cx={X(idxOf(pending.t))} cy={Y(pending.p)} r="4" fill="#f2b443" />}
        {pending && hov && <line x1={X(idxOf(pending.t))} y1={Y(pending.p)} x2={hov.x} y2={hov.y} stroke="#f2b443" strokeDasharray="4 3" />}
        {hv && <>
          <line x1={X(hov.i)} x2={X(hov.i)} y1={pad.t} y2={totalH - 4} className="cross" />
          {hov.y > pad.t && hov.y < H - pad.b && <>
            <line x1={pad.l} x2={w - pad.r} y1={hov.y} y2={hov.y} className="cross" />
            <rect x={w - pad.r} y={hov.y - 9} width={pad.r - 2} height="18" rx="3" fill="#334155" />
            <text x={w - pad.r + 4} y={hov.y + 4} fill="#fff" fontSize="11" className="mono">{P(hov.y).toFixed(digits)}</text>
          </>}
        </>}
        {lastC.close > lo && lastC.close < hi && <>
          <line x1={pad.l} x2={w - pad.r} y1={Y(lastC.close)} y2={Y(lastC.close)} stroke="var(--accent)" strokeDasharray="2 3" opacity="0.6" />
          <rect x={w - pad.r} y={Y(lastC.close) - 9} width={pad.r - 2} height="18" rx="3" fill="var(--accent)" />
          <text x={w - pad.r + 4} y={Y(lastC.close) + 4} fill="#fff" fontSize="11" className="mono">{lastC.close.toFixed(digits)}</text>
        </>}
        {panes.map((p, k) => {
          const top = H + 6 + k * (SUB + 6);
          if (p === "rsi") {
            const yf = (v) => subY(k, v, 0, 100);
            return (
              <g key={p}>
                <rect x={pad.l} y={top} width={plotW} height={SUB} className="panel" />
                {[30, 70].map((v) => <line key={v} x1={pad.l} x2={w - pad.r} y1={yf(v)} y2={yf(v)} className="grid" strokeDasharray="3 3" />)}
                <text x={w - pad.r + 4} y={yf(70) + 4} className="axis">70</text><text x={w - pad.r + 4} y={yf(30) + 4} className="axis">30</text>
                <text x={pad.l + 4} y={top + 12} className="axis">RSI 14</text>
                <path d={line("rsi", yf)} fill="none" stroke="#38bdf8" strokeWidth="1.3" />
              </g>
            );
          }
          const yf = (v) => subY(k, v, -mAbs, mAbs);
          return (
            <g key={p}>
              <rect x={pad.l} y={top} width={plotW} height={SUB} className="panel" />
              <line x1={pad.l} x2={w - pad.r} y1={yf(0)} y2={yf(0)} className="grid" />
              <text x={pad.l + 4} y={top + 12} className="axis">MACD 12/26/9</text>
              {vis.map((x, j) => x.macd_hist != null && <rect key={`h${x.time}`} x={X(start + j) - Math.max(0.6, cw * 0.3)} width={Math.max(1.2, cw * 0.6)}
                y={Math.min(yf(0), yf(x.macd_hist))} height={Math.abs(yf(x.macd_hist) - yf(0))} className={x.macd_hist >= 0 ? "hist-up" : "hist-down"} />)}
              <path d={line("macd", yf)} fill="none" stroke="#38bdf8" strokeWidth="1.2" />
              <path d={line("macd_signal", yf)} fill="none" stroke="#f2b443" strokeWidth="1.2" />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
