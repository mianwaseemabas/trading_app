import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, fmt, useAction, useApi } from "../components.jsx";

const MORE = [
  ["Gold", "GOLD", "XAUUSD"], ["Silver", "SILVER", "XAGUSD"], ["US oil (WTI)", "OIL_CRUDE", "XTIUSD"], ["Brent oil", "OIL_BRENT", "XBRUSD"],
  ["S&P 500", "US500", "US500"], ["Nasdaq 100", "US100", "NAS100"], ["Dow 30", "US30", "US30"], ["DAX 40", "DE40", "GER40"],
];

export function MoreMarketsCard() {
  const { can } = useAuth();
  const { data: strat, reload } = useApi("/system/strategy");
  const { data: br } = useApi("/brokers");
  const [run, busy] = useAction();
  if (!strat) return null;
  const capital = br?.active === "capital";
  const syms = strat.symbols || [];
  const set = (list) => run(() => api.put("/system/strategy", { values: { symbols: list } }), "Markets saved").then(reload).catch(() => {});
  return (
    <Card title="🥇 Gold, oil & indices (through your forex broker)">
      <p className="small muted">Tap to add or remove. Names differ per broker — shown here for {capital ? "Capital.com" : "MT5 / cTrader brokers"}; if your broker uses another name
        (e.g. USOIL instead of XTIUSD), type it on the Risk &amp; Strategy page. These markets are watched, charted and scanned by the strategy like forex pairs,
        and trade only through your broker (demo first). Gold and oil move more than currencies — the app sizes trades from your risk amount.</p>
      <div className="chips">
        {MORE.map(([label, cap, mt]) => {
          const s = capital ? cap : mt;
          const on = syms.includes(s);
          return <button key={s} className={`chip ${on ? "sel" : ""}`} disabled={!can("strategy.edit") || busy}
            onClick={() => set(on ? syms.filter((x) => x !== s) : [...syms, s])}>{on ? "✓ " : "+ "}{label} <span className="tiny muted">{s}</span></button>;
        })}
      </div>
    </Card>
  );
}

export function SaudiStocksCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/tadawul", { interval: 60000 });
  const [run, busy] = useAction();
  const [code, setCode] = useState("");
  if (!data) return null;
  const save = (list) => run(() => api.put("/tadawul", { symbols: list }), "Saved").then(() => api.post("/tadawul/refresh")).then(reload).catch(() => {});
  const add = () => {
    let c = code.trim().toUpperCase();
    if (/^\d{4}$/.test(c)) c += ".SR";
    if (c) save([...data.symbols, c]);
    setCode("");
  };
  return (
    <Card title="🇸🇦 Saudi stocks (Tadawul) — watch only" actions={<Badge tone={data.market_open ? "good" : "muted"}>{data.market_open ? "market open" : "market closed"}</Badge>}>
      <p className="small muted">{data.note} Trading hours: Sun–Thu 10:00–15:00 Riyadh.</p>
      <div className="chips">
        {data.symbols.map((s) => {
          const st = data.status[s] || {};
          return <span key={s} className={`chip ${st.ok === false ? "off" : "on"}`} title={st.error || `updated ${fmt.dt(st.at)}`}>
            {st.name || s} <span className="tiny muted">{s}</span>
            {can("strategy.edit") && <button className="chip-x" onClick={() => save(data.symbols.filter((x) => x !== s))}>✕</button>}
          </span>;
        })}
      </div>
      {can("strategy.edit") && <>
        <div className="row gap wrap" style={{ marginTop: 10 }}>
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Code, e.g. 2222 or 2222.SR" style={{ width: 200 }} onKeyDown={(e) => e.key === "Enter" && add()} />
          <button className="btn sm" disabled={busy || !code.trim()} onClick={add}>Add</button>
        </div>
        <div className="chips" style={{ marginTop: 8 }}>
          {Object.entries(data.popular).filter(([k]) => !data.symbols.includes(k)).map(([k, n]) => <button key={k} className="chip" disabled={busy} onClick={() => save([...data.symbols, k])}>+ {n}</button>)}
        </div>
      </>}
      <p className="tiny muted">They appear in the Terminal watchlist under “Stocks &amp; more” with charts, AI chart explanations and price-alert lines.</p>
    </Card>
  );
}
