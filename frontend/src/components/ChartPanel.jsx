import React, { useState } from "react";
import { api } from "../api.js";
import { useAction, useApi } from "../components.jsx";
import ZoomChart, { DEFAULT_IND } from "./ZoomChart.jsx";

export const TFS = [["S5", "5s"], ["S15", "15s"], ["S30", "30s"], ["M1", "1m"], ["M5", "5m"], ["M15", "15m"], ["M30", "30m"], ["H1", "1H"], ["H4", "4H"], ["D1", "1D"]];
export const TF_SEC = { S5: 5, S15: 15, S30: 30, M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };
const IND_LABEL = [["ema", "EMA 20/50"], ["bb", "Bollinger"], ["volume", "Volume"], ["rsi", "RSI"], ["macd", "MACD"], ["markers", "Signals ▲▼"]];
const TOOLS = [[null, "✋", "Move / zoom"], ["hline", "—", "Horizontal line"], ["trend", "／", "Trend line (2 taps)"], ["alert", "🔔", "Price alert line"]];

export function useIndicators(key = "q.ind") {
  const [ind, setInd] = useState(() => { try { return { ...DEFAULT_IND, ...JSON.parse(localStorage.getItem(key) || "{}") }; } catch { return DEFAULT_IND; } });
  const toggle = (k) => setInd((x) => { const n = { ...x, [k]: !x[k] }; try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* ignore */ } return n; });
  return [ind, toggle];
}

export function IndicatorChips({ ind, toggle }) {
  return <div className="ind-chips">{IND_LABEL.map(([k, l]) => <button key={k} className={`chip ${ind[k] ? "sel" : ""}`} onClick={() => toggle(k)}>{l}</button>)}</div>;
}

export default function ChartPanel({ symbol, tf, digits = 5, levels = [], compact = false, ind, toggleInd, showTools = true }) {
  const refreshMs = TF_SEC[tf] < 60 ? 2000 : TF_SEC[tf] <= 300 ? 5000 : 30000;
  const { data: cd } = useApi(`/intel/candles?symbol=${symbol}&tf=${tf}&limit=400`, { interval: refreshMs, deps: [symbol, tf] });
  const { data: dr, reload } = useApi(`/drawings?symbol=${symbol}`, { interval: 30000, deps: [symbol] });
  const [tool, setTool] = useState(null);
  const [sel, setSel] = useState(null);
  const [run] = useAction();
  const create = (d) => run(() => api.post("/drawings", { symbol, ...d }), d.alert ? "Price alert set 🔔" : "Line added").then(() => { reload(); setTool(null); }).catch(() => {});
  const draws = dr || [];
  return (
    <div className="chart-panel">
      {showTools && (
        <div className="chart-tools">
          <div className="tool-btns">{TOOLS.map(([k, icon, title]) => <button key={title} title={title} className={tool === k ? "on" : ""} onClick={() => setTool(k)}>{icon}<span className="hide-sm"> {title.split(" (")[0]}</span></button>)}</div>
          {toggleInd && !compact && <IndicatorChips ind={ind} toggle={toggleInd} />}
        </div>
      )}
      <ZoomChart candles={cd?.candles || []} markers={cd?.markers || []} digits={digits} levels={levels} tfSec={TF_SEC[tf]} ind={ind || DEFAULT_IND}
        drawings={draws} tool={tool} onCreate={create} selected={sel} onSelect={setSel} compact={compact} />
      {!compact && <div className="tiny muted">{ind?.ema !== false && <>— EMA 20 <span style={{ color: "#a78bfa" }}>— EMA 50</span> · </>}dashed = your open trade (entry / SL / TP){cd?.source && <> · data: {cd.source}</>}</div>}
      {showTools && draws.length > 0 && (
        <div className="draw-list">
          {draws.map((d) => (
            <div key={d.id} className={`draw-row ${sel === d.id ? "sel" : ""}`} onClick={() => setSel(d.id)}>
              <span className="swatch" style={{ background: d.color }} />
              <span className="small">{d.kind === "hline" ? `Line ${Number(d.data.price).toFixed(digits)}` : `Trend → now ${d.level_now != null ? Number(d.level_now).toFixed(digits) : "—"}`}
                {d.triggered_at && !d.alert && <span className="tiny muted"> · alert fired</span>}</span>
              <input className="draw-label" placeholder="label" defaultValue={d.label} onBlur={(e) => e.target.value !== d.label && api.put(`/drawings/${d.id}`, { label: e.target.value }).then(reload)} />
              <button className={`btn ghost sm ${d.alert ? "bell-on" : ""}`} title={d.alert ? "Alert on (fires once when price crosses)" : "Turn alert on"}
                onClick={(e) => { e.stopPropagation(); run(() => api.put(`/drawings/${d.id}`, { alert: !d.alert }), d.alert ? "Alert off" : "Alert on — you'll be notified once").then(reload); }}>{d.alert ? "🔔" : "🔕"}</button>
              <button className="btn ghost sm" title="Delete" onClick={(e) => { e.stopPropagation(); run(() => api.del(`/drawings/${d.id}`)).then(reload); }}>🗑</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
