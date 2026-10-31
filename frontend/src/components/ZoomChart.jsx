import React, { useEffect, useMemo, useRef, useState } from "react";
import { useWidth } from "../components.jsx";

/* Candle chart with zoom (wheel / pinch / buttons), pan (drag / swipe), crosshair and live follow. */
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function timeLabel(sec, tfSec) {
  const d = new Date(sec * 1000);
  if (tfSec >= 86400) return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  if (tfSec >= 3600) return `${d.toLocaleDateString(undefined, { day: "2-digit", month: "short" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
  if (tfSec >= 60) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function ZoomChart({ candles = [], digits = 5, levels = [], tfSec = 3600, height }) {
  const [ref, w] = useWidth();
  const mobile = w < 600;
  const H = height || (mobile ? 280 : 380);
  const pad = { l: 6, r: mobile ? 58 : 72, t: 10, b: 22 };
  const n = candles.length;
  const [view, setView] = useState({ count: mobile ? 50 : 90, end: 0, follow: true }); // end = index after last visible
  const [hov, setHov] = useState(null);
  const drag = useRef(null);
  const svgRef = useRef(null);

  // keep following the newest candle when the user hasn't scrolled back
  const end = view.follow ? n : clamp(view.end, Math.min(n, 5), n);
  const count = clamp(view.count, 10, Math.max(10, n));
  const start = Math.max(0, end - count);
  const vis = candles.slice(start, end);
  const plotW = w - pad.l - pad.r;
  const cw = plotW / Math.max(count, 1);

  const { lo, hi } = useMemo(() => {
    const vals = vis.flatMap((x) => [x.high, x.low]);
    const lv = levels.map((l) => l.price).filter((p) => p && vals.length && p > Math.min(...vals) * 0.98 && p < Math.max(...vals) * 1.02);
    let a = Math.min(...vals, ...lv), b = Math.max(...vals, ...lv);
    const m = (b - a) * 0.06 || a * 0.001 || 0.001;
    return { lo: a - m, hi: b + m };
  }, [vis, levels]);

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

  // non-passive wheel listener so the page does not scroll while zooming
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
  const ticks = [...Array(6).keys()].map((k) => lo + ((hi - lo) * (k + 0.5)) / 6);
  const line = (k) => vis.map((x, j) => (x[k] == null ? "" : `${j && vis[j - 1][k] != null ? "L" : "M"}${X(start + j).toFixed(1)},${Y(x[k]).toFixed(1)}`)).join("");
  const step = Math.max(1, Math.ceil(count / Math.max(2, Math.floor(plotW / (tfSec >= 3600 && tfSec < 86400 ? 110 : 80)))));
  const hv = hov && hov.i >= start && hov.i < end ? candles[hov.i] : null;
  const lastC = candles[n - 1];

  const pt = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    const t = e.touches ? e.touches[0] : e;
    return { x: t.clientX - r.left, y: t.clientY - r.top };
  };
  const onDown = (e) => {
    if (e.touches && e.touches.length === 2) {
      const d = Math.abs(e.touches[0].clientX - e.touches[1].clientX);
      drag.current = { pinch: d, count };
      return;
    }
    drag.current = { x: pt(e).x, end, moved: false };
  };
  const onMove = (e) => {
    if (drag.current?.pinch && e.touches?.length === 2) {
      const d = Math.abs(e.touches[0].clientX - e.touches[1].clientX) || 1;
      const nc = clamp(Math.round(drag.current.count * (drag.current.pinch / d)), 10, Math.max(10, n));
      setView((v) => ({ ...v, count: nc }));
      return;
    }
    const p = pt(e);
    if (drag.current && drag.current.x != null) {
      const dx = p.x - drag.current.x;
      if (Math.abs(dx) > 3) drag.current.moved = true;
      const ne = clamp(Math.round(drag.current.end - dx / cw), Math.min(n, 10), n);
      setView((v) => ({ ...v, end: ne, follow: ne >= n }));
      if (e.touches) return;
    }
    const i = Math.floor((p.x - pad.l) / cw) + start;
    setHov({ i: clamp(i, start, end - 1), y: p.y });
  };
  const onUp = () => { drag.current = null; };

  return (
    <div ref={ref} className="chart zchart">
      <div className="zbar">
        {hv ? (
          <span className="mono tiny">{timeLabel(hv.time, tfSec)} · O {hv.open.toFixed(digits)} H {hv.high.toFixed(digits)} L {hv.low.toFixed(digits)} C <b className={hv.close >= hv.open ? "pos" : "neg"}>{hv.close.toFixed(digits)}</b></span>
        ) : <span className="tiny muted">{mobile ? "Swipe to move · pinch to zoom" : "Scroll to zoom · drag to move · double-click to reset"}</span>}
        <span className="zbtns">
          <button title="Zoom out" onClick={() => setZoom(1.4)}>−</button>
          <button title="Zoom in" onClick={() => setZoom(1 / 1.4)}>+</button>
          <button title="Back to latest" className={view.follow ? "" : "on"} onClick={() => setView({ count: mobile ? 50 : 90, end: n, follow: true })}>⇥ Latest</button>
        </span>
      </div>
      <svg ref={svgRef} width={w} height={H} style={{ touchAction: "none", cursor: drag.current ? "grabbing" : "crosshair" }}
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => { onUp(); setHov(null); }}
        onTouchStart={onDown} onTouchMove={onMove} onTouchEnd={onUp}
        onDoubleClick={() => setView({ count: mobile ? 50 : 90, end: n, follow: true })}>
        {ticks.map((v) => <g key={v}><line x1={pad.l} x2={w - pad.r} y1={Y(v)} y2={Y(v)} className="grid" /><text x={w - pad.r + 4} y={Y(v) + 4} className="axis">{v.toFixed(digits)}</text></g>)}
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
        <path d={line("ema20")} fill="none" stroke="#38bdf8" strokeWidth="1.5" />
        <path d={line("ema50")} fill="none" stroke="#a78bfa" strokeWidth="1.2" opacity="0.7" />
        {levels.filter((l) => l.price && l.price > lo && l.price < hi).map((l, k) => (
          <g key={k}><line x1={pad.l} x2={w - pad.r} y1={Y(l.price)} y2={Y(l.price)} stroke={l.color} strokeDasharray="5 4" />
            <text x={w - pad.r + 4} y={Y(l.price) - 3} className="axis" fill={l.color}>{l.label}</text></g>
        ))}
        {hv && <>
          <line x1={X(hov.i)} x2={X(hov.i)} y1={pad.t} y2={H - pad.b} className="cross" />
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
      </svg>
    </div>
  );
}
