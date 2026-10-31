import { MoreMarketsCard, SaudiStocksCard } from "../components/Markets.jsx";
import React from "react";
import { Badge, Card, Table, countdown, digitsFor, fmt, pnlClass, useApi } from "../components.jsx";

const tone = (t) => (t === "up" || t === "bullish" ? "ok" : t === "down" || t === "bearish" ? "bad" : "muted");
const arrow = (t) => ({ up: "▲ up", down: "▼ down", mixed: "◆ mixed", unknown: "needs more history" }[t] || "—");

export default function Overview() {
  const { data, error } = useApi("/market/overview", { interval: 30000 });
  const { data: waiting } = useApi("/setups?status=WAITING", { interval: 15000 });
  if (error) return <div className="alert bad">{error}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  const s = data.sessions;
  return (
    <div className="stack">
      {waiting?.setups?.length > 0 && <div className="alert warn"><b>{waiting.setups.length} setup(s) waiting for your approval.</b> <a href="#/setups">Review now →</a></div>}
      <MoreMarketsCard />
      <SaudiStocksCard />
      <Card title="Trading sessions (UTC, approximate)" actions={<Badge tone={s.weekend ? "muted" : s.liquidity.startsWith("high") ? "ok" : s.liquidity.startsWith("low") ? "warn" : "info"}>Liquidity: {s.liquidity}</Badge>}>
        <div className="chips">
          {s.sessions.map((x) => <span key={x.name} className={`chip ${x.open ? "on" : ""}`}>{x.open ? "● " : "○ "}{x.name} {x.utc}</span>)}
        </div>
        <p className="small muted">Most EURUSD/GBPUSD movement happens in London and the London–New York overlap (≈12:00–16:00 UTC = 15:00–19:00 Riyadh). Asian hours are usually quieter with wider spreads.</p>
      </Card>
      <Card title="Markets — top-down view (H4 context → H1 structure → M15 entry)">
        <Table rows={data.symbols.map((x) => ({ ...x, id: x.symbol }))} cols={[
          { label: "Market", render: (x) => <b>{x.symbol}</b> },
          { label: "Price", num: true, render: (x) => <span className="mono">{fmt.px(x.bid, digitsFor(x.symbol))}</span> },
          { label: "24h", num: true, render: (x) => <span className={pnlClass(x.change_24h_pct)}>{fmt.pct(x.change_24h_pct, 2)}</span> },
          { label: "Spread", num: true, render: (x) => (x.spread_points != null ? `${(x.spread_points / 10).toFixed(1)} pips` : "—") },
          ...["H4", "H1", "M15"].map((tf) => ({ label: `${tf} trend`, render: (x) => (x.context[tf] ? <Badge tone={tone(x.context[tf].trend)}>{arrow(x.context[tf].trend)}</Badge> : <span className="muted small">no data</span>) })),
          { label: "H1 structure", render: (x) => (x.context.H1 ? <Badge tone={tone(x.context.H1.structure)}>{x.context.H1.structure}</Badge> : "—") },
          { label: "Volatility (H1 ATR)", num: true, render: (x) => (x.context.H1 ? `${((x.context.H1.atr || 0) / (x.symbol.endsWith("JPY") ? 0.01 : 0.0001)).toFixed(1)} pips` : "—") },
          { label: "Alignment", render: (x) => <Badge tone={x.alignment === "mixed" ? "muted" : x.alignment.includes("up") ? "ok" : "bad"}>{x.alignment}</Badge> },
          { label: "Next high-impact news", render: (x) => (x.news[0] ? <span className="small">{x.news[0].currency} {x.news[0].title} <span className="muted">{countdown(x.news[0].time)}</span></span> : <span className="muted small">none in 24h</span>) },
        ]} />
        <p className="small muted">Trend = close and EMA fast both on the same side of EMA slow. Structure = last SMC break (BOS/CHoCH) direction. "Aligned" means all three timeframes agree — trades with the higher timeframe are usually easier. Click <a href="#/smc">SMC scanner</a> to see the levels on a chart.</p>
      </Card>
      <div className="grid two">
        {data.symbols.map((x) => x.context.H1 && (
          <Card key={x.symbol} title={`${x.symbol} — key liquidity (H1)`}>
            <div className="small">
              <div><span className="muted">Buy-side liquidity above (BSL): </span><span className="mono">{x.context.H1.bsl.map((v) => v.toFixed(digitsFor(x.symbol))).join(" · ") || "—"}</span></div>
              <div><span className="muted">Sell-side liquidity below (SSL): </span><span className="mono">{x.context.H1.ssl.map((v) => v.toFixed(digitsFor(x.symbol))).join(" · ") || "—"}</span></div>
              <div><span className="muted">RSI H1: </span>{fmt.num(x.context.H1.rsi, 1)} · <span className="muted">H4 RSI: </span>{fmt.num(x.context.H4?.rsi, 1)}</div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
