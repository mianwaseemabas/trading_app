import React, { useMemo, useState } from "react";
import { Badge, Card, CandleChart, Table, digitsFor, fmt, useApi } from "../components.jsx";

const TFS = ["M15", "H1", "H4", "D1"];

export default function Market() {
  const { data: strat } = useApi("/system/strategy");
  const symbols = strat?.symbols || ["EURUSD"];
  const [symbol, setSymbol] = useState(null);
  const [tf, setTf] = useState("H1");
  const [limit, setLimit] = useState(200);
  const sym = symbol || symbols[0];
  const { data, error } = useApi(`/market/candles?symbol=${sym}&timeframe=${tf}&limit=${limit}`, { interval: 30000, deps: [sym, tf, limit] });
  const { data: sigs } = useApi(`/signals?symbol=${sym}&actionable=true&limit=300`, { deps: [sym] });
  const { data: inst } = useApi("/market/instruments");
  const markers = useMemo(() => (sigs || []).filter((s) => s.timeframe === tf).map((s) => ({ time: Math.floor(new Date(s.candle_time).getTime() / 1000), action: s.action, status: s.status })), [sigs, tf]);
  return (
    <div className="stack">
      <Card
        title={`${sym} · ${tf}`}
        actions={
          <>
            <select value={sym} onChange={(e) => setSymbol(e.target.value)}>{symbols.map((s) => <option key={s}>{s}</option>)}</select>
            <select value={tf} onChange={(e) => setTf(e.target.value)}>{TFS.map((t) => <option key={t}>{t}</option>)}</select>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[100, 200, 400, 800].map((n) => <option key={n} value={n}>{n} candles</option>)}</select>
          </>
        }
      >
        {error && <div className="alert bad">{error}</div>}
        {data && data.gaps?.missing > 0 && <div className="alert warn small">{data.gaps.missing} weekday candle(s) missing in this window (largest gap {data.gaps.largest_gap_candles}).</div>}
        <CandleChart candles={data?.candles} digits={digitsFor(sym)} markers={markers} />
        <p className="muted small">Candles are closed bars (broker time converted to UTC). Triangles mark strategy BUY/SELL signals. Indicators: EMA {strat?.ema_fast}/{strat?.ema_slow}, RSI {strat?.rsi_period}, MACD {strat?.macd_fast}/{strat?.macd_slow}/{strat?.macd_signal}, ATR {strat?.atr_period}.</p>
      </Card>
      <div className="grid two">
        <Card title="Instrument specifications">
          <Table rows={inst?.instruments} rowKey="symbol" cols={[
            { label: "Symbol", k: "symbol" },
            { label: "Contract", k: "contract_size", num: true },
            { label: "Point", k: "point", num: true },
            { label: "Tick value", num: true, render: (i) => (i.tick_value ? fmt.num(i.tick_value, 4) : "—") },
            { label: "Lots min/step/max", render: (i) => `${i.volume_min} / ${i.volume_step} / ${i.volume_max}` },
            { label: "Source", render: (i) => <Badge tone={i.source === "mt5" ? "ok" : "warn"} title={i.source === "mt5" ? "Reported by broker" : "Default assumption until the bridge reports broker specs"}>{i.source}</Badge> },
          ]} />
        </Card>
        <Card title="Stored history">
          <Table rows={inst?.history} rowKey="timeframe" cols={[
            { label: "Symbol", k: "symbol" },
            { label: "TF", k: "timeframe" },
            { label: "Candles", k: "count", num: true },
            { label: "From", render: (h) => fmt.dt(h.first) },
            { label: "To", render: (h) => fmt.dt(h.last) },
          ]} />
        </Card>
      </div>
    </div>
  );
}
