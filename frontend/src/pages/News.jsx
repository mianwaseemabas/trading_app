import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, countdown, fmt, impactTone, useAction, useApi } from "../components.jsx";

const CCYS = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY"];

export default function News() {
  const { can } = useAuth();
  const [impact, setImpact] = useState("High,Medium");
  const [ccy, setCcy] = useState("");
  const { data, reload } = useApi(`/news?days_back=1&days_ahead=7&impact=${impact}${ccy ? `&currency=${ccy}` : ""}`, { interval: 60000, deps: [impact, ccy] });
  const [run, busy] = useAction();
  if (!data) return <p className="muted">Loading…</p>;
  const now = Date.now();
  const f = data.filter;
  let lastDay = "";
  return (
    <div className="stack">
      <Card title="Is news blocking trading right now?" actions={<a href="#/live" className="small">Change filter in Live trading → controls</a>}>
        <p className="small muted">
          News filter is <b className={f.news_filter ? "pos" : "neg"}>{f.news_filter ? "ON" : "OFF"}</b>: no new trades on a currency from {f.news_before_min} min before
          until {f.news_after_min} min after <b>{f.news_impacts.join(" / ")}</b> impact news. Open trades keep their stop-loss.
        </p>
        <div className="chips">
          {Object.entries(data.symbols).map(([s, v]) => (
            <span key={s} className={`chip ${v.blocked ? "off" : "on"}`} title={v.detail}>{s} {v.blocked ? "blocked" : "ok"}</span>
          ))}
        </div>
        {Object.values(data.symbols).filter((v) => v.blocked).map((v, i) => <p key={i} className="small neg">{v.detail}</p>)}
      </Card>
      <Card title="Economic calendar (this week & next)" actions={
        <>
          <select value={impact} onChange={(e) => setImpact(e.target.value)}>
            <option value="High">High impact only</option><option value="High,Medium">High + Medium</option><option value="High,Medium,Low">All</option>
          </select>
          <select value={ccy} onChange={(e) => setCcy(e.target.value)}><option value="">All currencies</option>{CCYS.map((c) => <option key={c}>{c}</option>)}</select>
          {can("system.settings") && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post("/news/refresh"), "Calendar refreshed").then(reload)}>Refresh</button>}
        </>
      }>
        <p className="muted small">
          Source: ForexFactory calendar · {data.status.ok ? `updated ${fmt.dt(data.status.last_fetch)} · ${data.status.count} events` : `not loaded${data.status.error ? `: ${data.status.error}` : ""}`}.
          {" "}<Badge tone="bad">High</Badge> events (interest rates, jobs/NFP, inflation/CPI) can move prices 50–150 pips in seconds — spreads widen and stops can slip.
        </p>
        {data.events.length === 0 && <p className="muted">No events for this filter.</p>}
        {data.events.map((e, i) => {
          const t = new Date(e.time).getTime();
          const day = new Date(e.time).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
          const head = day !== lastDay; lastDay = day;
          const soon = t > now && t - now < 2 * 3600 * 1000;
          return (
            <React.Fragment key={i}>
              {head && <h4>{day}</h4>}
              <div className={`news-row ${t < now ? "past" : ""} ${soon && e.impact === "High" ? "soon" : ""}`}>
                <span className="mono">{new Date(e.time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                <span className="small muted">{countdown(e.time)}</span>
                <b>{e.currency}</b>
                <span><Badge tone={impactTone(e.impact)}>{e.impact}</Badge> {e.title}</span>
                <span className="small muted hide-sm">fcst {e.forecast || "—"}</span>
                <span className="small muted hide-sm">prev {e.previous || "—"}</span>
              </div>
            </React.Fragment>
          );
        })}
      </Card>
      <Headlines />
      <Card title="How to use news (expert tip)">
        <ul className="small">
          <li>Before trading a pair, check both of its currencies here (EURUSD = EUR and USD).</li>
          <li>Beginners: don't open trades 30 minutes before a red (High) event — keep the news filter ON.</li>
          <li>If you hold a trade into big news, make sure the stop-loss is set and the size is small.</li>
          <li>"Forecast" vs the real result decides the direction: a much better-than-forecast number usually strengthens that currency.</li>
        </ul>
      </Card>
    </div>
  );
}

function Headlines() {
  const { can } = useAuth();
  const [impact, setImpact] = useState("High,Medium");
  const [mine, setMine] = useState(true);
  const [cat, setCat] = useState("");
  const { data, reload } = useApi(`/news/headlines?hours=48&impact=${impact}&mine=${mine}${cat ? `&category=${encodeURIComponent(cat)}` : ""}`,
    { interval: 120000, deps: [impact, mine, cat] });
  const [run, busy] = useAction();
  return (
    <Card title="Market headlines (central banks, forex, crypto, business)" actions={
      <>
        <select value={impact} onChange={(e) => setImpact(e.target.value)}>
          <option value="High">High impact only</option><option value="High,Medium">High + Medium</option><option value="High,Medium,Low">All</option>
        </select>
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">All sources</option><option value="central bank">Central banks</option><option value="forex">Forex</option>
          <option value="crypto">Crypto</option><option value="business">Business</option>
        </select>
        <label className="small"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> only my markets</label>
        {can("system.settings") && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post("/news/headlines/refresh"), "Headlines refreshed").then(reload)}>Refresh</button>}
      </>
    }>
      {!data ? <p className="muted">Loading…</p> : <>
        <p className="muted small">
          Updated every 10 minutes. {data.note} High-impact headlines about your markets ({data.watched.join(", ")}) also arrive as alerts
          (Telegram/email if switched on in <a href="#/alerts">Alerts</a>).
        </p>
        <div className="chips">
          {Object.entries(data.status.feeds || {}).map(([n, v]) => (
            <span key={n} className={`chip ${v.ok ? "on" : "off"}`} title={v.ok ? `${v.items} recent items` : v.error}>{n} {v.ok ? "✓" : "✕"}</span>
          ))}
        </div>
        {data.items.length === 0 && <p className="muted">No headlines for this filter yet.</p>}
        {data.items.map((h) => (
          <div key={h.id} className="headline">
            <div>
              <Badge tone={impactTone(h.impact)}>{h.impact}</Badge>{" "}
              <a href={h.url} target="_blank" rel="noopener noreferrer"><b>{h.title}</b></a>
            </div>
            <div className="small muted">
              {h.source} · {fmt.dt(h.published)} {h.assets.length > 0 && <>· affects <b>{h.assets.join(", ")}</b></>}
              {h.tags.length > 0 && <> · keywords: {h.tags.join(", ")}</>}
            </div>
            {h.summary && <div className="small">{h.summary.slice(0, 220)}{h.summary.length > 220 ? "…" : ""}</div>}
          </div>
        ))}
      </>}
    </Card>
  );
}
