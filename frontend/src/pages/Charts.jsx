import React, { useState } from "react";
import ChartPanel, { IndicatorChips, TFS, useIndicators } from "../components/ChartPanel.jsx";
import { useApi } from "../components.jsx";

const LAYOUTS = [[1, "1 chart"], [2, "2 charts"], [4, "4 charts"]];
const PRESETS = [["H4", "H1", "M15", "M5"], ["H1", "M5", "H4", "D1"]];

function load() {
  try { return JSON.parse(localStorage.getItem("q.charts") || "null"); } catch { return null; }
}

export default function Charts() {
  const { data: wl } = useApi("/intel/watchlist", { interval: 30000 });
  const saved = load();
  const [layout, setLayout] = useState(saved?.layout || 2);
  const [panels, setPanels] = useState(saved?.panels || [0, 1, 2, 3].map((i) => ({ symbol: "", tf: PRESETS[0][i] })));
  const [ind, toggleInd] = useIndicators("q.ind.multi");
  const syms = (wl || []).map((w) => w.symbol);
  const save = (l, p) => { try { localStorage.setItem("q.charts", JSON.stringify({ layout: l, panels: p })); } catch { /* ignore */ } };
  const set = (i, patch) => { const p = panels.map((x, k) => (k === i ? { ...x, ...patch } : x)); setPanels(p); save(layout, p); };
  const sameSymbol = (s) => { const p = panels.map((x) => ({ ...x, symbol: s })); setPanels(p); save(layout, p); };
  if (!wl) return <p className="muted">Loading…</p>;
  const first = panels[0].symbol || syms[0];
  return (
    <div className="stack">
      <div className="row between wrap gap">
        <div className="seg">{LAYOUTS.map(([k, l]) => <button key={k} className={layout === k ? "active" : ""} onClick={() => { setLayout(k); save(k, panels); }}>{l}</button>)}</div>
        <div className="row gap wrap small">
          <span className="muted">All charts:</span>
          <select value="" onChange={(e) => e.target.value && sameSymbol(e.target.value)}><option value="">same market…</option>{syms.map((s) => <option key={s}>{s}</option>)}</select>
          <button className="btn sm ghost" onClick={() => { const p = panels.map((x, i) => ({ ...x, tf: PRESETS[0][i] })); setPanels(p); save(layout, p); }}>4H · 1H · 15m · 5m</button>
        </div>
      </div>
      <IndicatorChips ind={ind} toggle={toggleInd} />
      <div className={`multi-grid l${layout}`}>
        {panels.slice(0, layout).map((p, i) => {
          const sym = p.symbol || (i === 0 ? first : first);
          const w = (wl || []).find((x) => x.symbol === sym);
          return (
            <div key={i} className="tcard multi-cell">
              <div className="row between wrap gap">
                <select value={sym} onChange={(e) => set(i, { symbol: e.target.value })}>{syms.map((s) => <option key={s}>{s}</option>)}</select>
                <div className="tf-row scroll-x">{TFS.map(([k, l]) => <button key={k} className={p.tf === k ? "on" : ""} onClick={() => set(i, { tf: k })}>{l}</button>)}</div>
              </div>
              <ChartPanel symbol={sym} tf={p.tf} digits={w?.digits ?? 5} ind={ind} compact={layout > 1} showTools={layout === 1} />
            </div>
          );
        })}
      </div>
      <p className="tiny muted">Tip: the same market on 4H, 1H, 15m and 5m shows the big trend, the structure and the entry timing at once. Lines you draw on the Terminal appear here too.</p>
    </div>
  );
}
