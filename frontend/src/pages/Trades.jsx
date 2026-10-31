import React, { useState } from "react";
import { Badge, Card, LineChart, Stat, Table, Tabs, digitsFor, fmt, modeLabel, modeTone, pnlClass, statusTone, useApi } from "../components.jsx";
import { PositionDetail } from "./Live.jsx";

export function MetricsGrid({ m }) {
  if (!m || !m.trades) return <p className="muted">No closed trades in this period.</p>;
  return (
    <div className="grid stats">
      <Stat label="Trades" value={m.trades} sub={`${m.wins}W / ${m.losses}L · ${m.long_trades} long / ${m.short_trades} short`} />
      <Stat label="Net P&L" value={<span className={pnlClass(m.net_pnl)}>{fmt.money(m.net_pnl)}</span>} sub={`${fmt.pct(m.return_pct)} · after costs`} />
      <Stat label="Win rate" value={fmt.pct(m.win_rate_pct, 1)} />
      <Stat label="Profit factor" value={m.profit_factor === null ? "—" : fmt.num(m.profit_factor, 2)} sub={`GP ${fmt.money(m.gross_profit)} / GL ${fmt.money(m.gross_loss)}`} />
      <Stat label="Expectancy / trade" value={<span className={pnlClass(m.expectancy)}>{fmt.money(m.expectancy)}</span>} sub={m.avg_r !== null && m.avg_r !== undefined ? `avg ${fmt.num(m.avg_r, 2)} R` : ""} />
      <Stat label="Max drawdown" value={fmt.money(m.max_drawdown)} sub={`${fmt.pct(m.max_drawdown_pct)} · losing streak ${m.longest_losing_streak}`} />
    </div>
  );
}

const ORDER_COLS = [
  { label: "Created", render: (o) => fmt.dt(o.created_at) },
  { label: "Client ID", k: "client_id" },
  { label: "Mode", render: (o) => <Badge tone={modeTone(o.mode)}>{modeLabel(o.mode)}</Badge> },
  { label: "Type", k: "action" },
  { label: "Symbol", k: "symbol" },
  { label: "Side", render: (o) => <Badge tone={statusTone(o.side)}>{o.side}</Badge> },
  { label: "Lots", k: "volume", num: true },
  { label: "Req.", num: true, render: (o) => fmt.px(o.requested_price, digitsFor(o.symbol)) },
  { label: "Fill", num: true, render: (o) => fmt.px(o.fill_price, digitsFor(o.symbol)) },
  { label: "Slip (pts)", k: "slippage_points", num: true },
  { label: "SL ✓", render: (o) => (o.status === "FILLED" ? (o.sl_verified ? "✓" : <span className="neg">✕</span>) : "") },
  { label: "Risk", num: true, render: (o) => fmt.money(o.risk_amount) },
  { label: "Status", render: (o) => <Badge tone={statusTone(o.status)} title={o.error}>{o.status}</Badge> },
];

const CLOSED_COLS = [
  { label: "Closed", render: (p) => fmt.dt(p.closed_at) },
  { label: "Mode", render: (p) => <Badge tone={modeTone(p.mode)}>{modeLabel(p.mode)}</Badge> },
  { label: "Symbol", k: "symbol" },
  { label: "Side", render: (p) => <Badge tone={statusTone(p.side)}>{p.side}</Badge> },
  { label: "Lots", k: "volume", num: true },
  { label: "Open", num: true, render: (p) => fmt.px(p.open_price, digitsFor(p.symbol)) },
  { label: "Close", num: true, render: (p) => fmt.px(p.close_price, digitsFor(p.symbol)) },
  { label: "Reason", k: "close_reason" },
  { label: "Commission", num: true, render: (p) => fmt.money(p.commission) },
  { label: "Swap", num: true, render: (p) => fmt.money(p.swap) },
  { label: "Net P&L", num: true, render: (p) => <span className={pnlClass(p.realized_pnl)}>{fmt.money(p.realized_pnl)}</span> },
];

const GROUP_LABEL = { symbol: "By market", side: "Buy vs sell", source: "Automatic vs manual", weekday: "By weekday", hour: "By hour (UTC)" };

function Breakdown({ data }) {
  if (!data) return null;
  return (
    <div className="grid two">
      {Object.entries(data).filter(([, d]) => Object.keys(d).length).map(([g, d]) => (
        <Card key={g} title={GROUP_LABEL[g] || g}>
          <Table rows={Object.entries(d).map(([k, m]) => ({ id: k, k, ...m }))} cols={[
            { label: "", k: "k" }, { label: "Trades", k: "trades", num: true },
            { label: "Win %", num: true, render: (m) => fmt.pct(m.win_rate_pct, 0) },
            { label: "Net P&L", num: true, render: (m) => <span className={pnlClass(m.net_pnl)}>{fmt.money(m.net_pnl)}</span> },
            { label: "Avg R", num: true, render: (m) => fmt.num(m.avg_r, 2) },
          ]} />
        </Card>
      ))}
    </div>
  );
}

export default function Trades() {
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState("performance");
  const [mode, setMode] = useState("PAPER");
  const [days, setDays] = useState(30);
  const { data: perf } = useApi(`/performance?mode=${mode}&days=${days}`, { deps: [mode, days] });
  const { data: orders } = useApi(tab === "orders" ? "/orders?limit=300" : null, { interval: 10000, deps: [tab] });
  const { data: closed } = useApi(tab === "closed" ? "/positions?status=CLOSED&limit=500" : null, { deps: [tab] });
  return (
    <div className="stack">
      <Tabs value={tab} onChange={setTab} tabs={[{ k: "performance", label: "Performance" }, { k: "orders", label: "Orders" }, { k: "closed", label: "Closed positions" }]} />
      {tab === "performance" && (
        <Card title="Realized performance" actions={
          <>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>{["PAPER", "MT5_DEMO", "MT5_LIVE"].map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}</select>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map((d) => <option key={d} value={d}>{d} days</option>)}</select>
            <a className="btn sm" href={`/api/positions/export.csv?mode=${mode}`}>⬇ Export CSV</a>
          </>
        }>
          <MetricsGrid m={perf?.metrics} />
          <LineChart series={[{ points: (perf?.equity_curve || []).map((p) => ({ x: new Date(p.time).getTime() / 1000, y: p.equity })) }]} yFormat={(v) => fmt.money(v)} />
          <p className="muted small">{perf?.note}</p>
        </Card>
      )}
      {tab === "performance" && <Breakdown data={perf?.breakdown} />}
      {tab === "orders" && <Card title="Orders"><Table rows={orders} cols={ORDER_COLS} /></Card>}
      {tab === "closed" && <Card title="Closed positions — click a row for chart & journal" actions={<a className="btn sm" href="/api/positions/export.csv">⬇ Export CSV</a>}><Table rows={closed} cols={CLOSED_COLS} onRow={(p) => setDetail(p.id)} /></Card>}
      {detail && <PositionDetail id={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
