import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { ExplainChart } from "../components/Helpers.jsx";
import ChartPanel, { TFS, TF_SEC, useIndicators } from "../components/ChartPanel.jsx";
import { Modal, fmt, pnlClass, useAction, useApi, useToast, useWidth } from "../components.jsx";

const ASSET_TABS = [["all", "All"], ["fav", "★"], ["forex", "Forex"], ["crypto", "Crypto"], ["other", "Stocks & more"]];
const COIN = { BTC: "₿", ETH: "Ξ", SOL: "◎", XRP: "✕", BNB: "◆", DOGE: "Ð", ADA: "₳" };
const toneCls = { ok: "t-ok", bad: "t-bad", warn: "t-warn", info: "t-info", muted: "t-muted" };

function getSym() {
  const m = window.location.hash.match(/[?&]s=([A-Z0-9_]+)/);
  return m ? m[1] : null;
}

function SymbolBadge({ symbol, asset }) {
  const base = asset === "crypto" ? symbol.replace(/(USDT|USDC)$/, "") : symbol.slice(0, 3);
  return <span className={`sym-badge a-${asset?.split(" ")[0]}`}>{COIN[base] || base.slice(0, 2)}</span>;
}

function TopStats({ t, symbol }) {
  const a = t?.account || {};
  const cell = (l, v, cls = "") => <div className="ts-cell"><span>{l}</span><b className={cls}>{v}</b></div>;
  return (
    <div className="term-top">
      <div className="row gap">
        <SymbolBadge symbol={symbol} asset={t?.asset} />
        <div><div className="term-sym">{symbol}</div><div className="small muted">{t?.asset} · <RouteTag r={t?.route} /></div></div>
      </div>
      <div className="ts-row">
        {cell("Account", `${a.mode_label || "—"}${a.venue && a.venue !== "paper" ? ` · ${a.venue}` : ""}`)}
        {cell("Balance", fmt.money(a.balance))}
        {cell("Equity", fmt.money(a.equity))}
        {cell("Open P/L", fmt.money(a.open_pl), pnlClass(a.open_pl))}
        {cell(t?.venue === "crypto" ? "In coins" : "Margin", fmt.money(a.margin))}
        {cell(t?.venue === "crypto" ? "Cash (USDT)" : "Free margin", fmt.money(a.free_margin))}
        {t?.venue !== "crypto" && cell("Margin level", a.margin_level ? `${a.margin_level}%` : "—")}
        {cell("Leverage", a.leverage ? (a.leverage === 1 ? "1:1 spot" : `1:${a.leverage}`) : "—")}
      </div>
      {a.error && <span className="small t-warn">{a.error}</span>}
    </div>
  );
}

export function RouteTag({ r }) {
  if (!r) return null;
  return <span className={`route-tag r-${r.kind}`} title={`${r.label}${r.auto ? " · auto-trade ON" : ""}`}>{r.auto && r.kind !== "watch" ? "⚡" : ""}{r.short}</span>;
}

function Watchlist({ items, symbol, onPick, onFav }) {
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("all");
  const rows = (items || []).filter((w) => w.symbol.includes(q.toUpperCase()))
    .filter((w) => tab === "all" || (tab === "fav" ? w.favorite : tab === "other" ? !["forex", "crypto"].includes(w.asset) : w.asset === tab));
  return (
    <aside className="wl">
      <input className="wl-search" placeholder="🔍  Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="wl-tabs">{ASSET_TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      <div className="wl-list">
        {rows.length === 0 && <p className="muted small pad">Nothing here. Add markets on Risk & Strategy (broker) or Brokers → Crypto.</p>}
        {rows.map((w) => (
          <div key={w.symbol} className={`wl-row ${w.symbol === symbol ? "on" : ""}`} onClick={() => onPick(w.symbol)}>
            <div className="wl-l">
              <b>{w.symbol}</b> <RouteTag r={w.route} />
              <span className="mono small muted">{w.bid != null ? Number(w.bid).toFixed(w.digits) : "—"}</span>
            </div>
            <div className="wl-r">
              <span className={`star ${w.favorite ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); onFav(w.symbol); }}>★</span>
              <span className={`small ${pnlClass(w.change_24h)}`}>{w.change_24h != null ? `${w.change_24h > 0 ? "↗" : "↘"} ${w.change_24h.toFixed(2)}%` : ""}</span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function Highlights({ t }) {
  const b = t?.highlights?.best, a = t?.account || {};
  return (
    <div className="tcard">
      <div className="tc-title">Today's highlights</div>
      <div className="hl ok">
        <div className="small muted">🏆 Best performer (24h)</div>
        <b>{b?.symbol || "—"}</b>
        <div className={`small ${pnlClass(b?.change_24h)}`}>{b?.change_24h != null ? `${b.change_24h > 0 ? "+" : ""}${b.change_24h.toFixed(2)}%` : "no data"}</div>
      </div>
      <div className="hl info">
        <div className="small muted">$ Today's P/L ({a.mode_label || "—"})</div>
        <b className={pnlClass(a.today_pl)}>{a.today_pl != null ? `${a.today_pl > 0 ? "+" : ""}${fmt.money(a.today_pl)}` : "—"}</b>
        <div className={`small ${pnlClass(a.today_roi)}`}>{a.today_roi != null ? `${a.today_roi > 0 ? "+" : ""}${a.today_roi}% of start-of-day equity` : ""}</div>
      </div>
    </div>
  );
}

function Bar({ value, color = "var(--ok)", rest = "var(--line2)" }) {
  return <div className="tbar" style={{ background: rest }}><div style={{ width: `${Math.max(0, Math.min(100, value || 0))}%`, background: color }} /></div>;
}

function Sentiment({ t }) {
  const s = t?.sentiment;
  if (!s) return <div className="tcard"><div className="tc-title">Market sentiment</div></div>;
  const m = s.momentum, br = s.breadth, fg = s.crypto_fear_greed;
  const mcol = m.value == null ? "var(--muted)" : m.value < 45 ? "var(--bad)" : m.value > 55 ? "var(--ok)" : "var(--warn)";
  return (
    <div className="tcard">
      <div className="tc-title">Market sentiment</div>
      <div className="row between small"><span title={m.how}>Momentum (fear ↔ greed)</span><b>{m.value ?? "—"}</b></div>
      <Bar value={m.value} color={mcol} />
      <div className="small" style={{ color: mcol }}>{m.label}</div>
      <div className="row between small" style={{ marginTop: 10 }}><span title={br.how}>Bulls / bears (H1 trend)</span><b>{br.up_pct != null ? `${br.up_pct}% / ${100 - br.up_pct}%` : "—"}</b></div>
      <Bar value={br.up_pct} rest={br.up_pct != null ? "var(--bad)" : "var(--line2)"} />
      <div className={`bias ${s.bias === "Bullish" ? "t-ok" : s.bias === "Bearish" ? "t-bad" : "t-muted"}`}>{s.bias === "Bullish" ? "↗" : s.bias === "Bearish" ? "↘" : "↔"} {s.bias}</div>
      {fg && <div className="small muted" style={{ marginTop: 6 }}>Crypto Fear & Greed: <b>{fg.value}</b> {fg.label}</div>}
    </div>
  );
}

function Insights({ t, onBrief }) {
  const i = t?.insights;
  return (
    <div className="tcard">
      <div className="tc-title row between"><span>✦ Trade intelligence</span>{onBrief && <button className="btn sm ghost" onClick={onBrief}>AI brief</button>}</div>
      <div className="ins-list">
        {(i?.items || []).slice(0, 3).map((x, k) => (
          <a key={k} href={x.link || undefined} className={`ins ${toneCls[x.tone]}`}>{{ ok: "↗", bad: "↘", warn: "⚠", info: "●", muted: "·" }[x.tone]} {x.text}</a>
        ))}
      </div>
      {i && (
        <>
          <div className="row between small" style={{ marginTop: 8 }} title={i.how}>
            <span>Signal agreement · {i.direction}</span><b>{i.agreement}%</b>
          </div>
          <Bar value={i.agreement} color={i.direction === "bearish" ? "var(--bad)" : i.direction === "bullish" ? "var(--ok)" : "var(--muted)"} />
          <div className="tiny muted">{i.votes.bullish} bullish · {i.votes.bearish} bearish of {i.votes.total} factors — not a prediction</div>
        </>
      )}
    </div>
  );
}

function Achievements({ t }) {
  const a = t?.achievements;
  const icon = { first: "☆", ten: "◎", journal: "✎", plan: "⛨", review: "✓", master: "♛" };
  return (
    <div className="tcard">
      <div className="tc-title">Discipline achievements</div>
      <div className="ach-row">
        {(a?.items || []).map((x) => <div key={x.key} className={`ach ${x.done ? "done" : ""}`} title={`${x.label}: ${x.how} (${x.progress}/${x.goal})`}><span>{icon[x.key]}</span><small>{x.label}</small></div>)}
      </div>
      {a && <><div className="row between small"><span>Level {a.level}</span><span>{a.progress_pct}%</span></div><Bar value={a.progress_pct} color="linear-gradient(90deg,#f59e0b,#f97316)" /><div className="tiny muted">Next: {a.next}</div></>}
    </div>
  );
}

function RiskStrip({ t }) {
  const r = t?.risk;
  if (!r) return null;
  const col = r.level === "High" ? "var(--bad)" : r.level === "Medium" ? "var(--warn)" : "var(--ok)";
  return (
    <div className="tcard risk-strip">
      <div className="row gap">
        <span className="shield" style={{ borderColor: col, color: col }}>⛨</span>
        <div><div className="small muted">Risk score · {t.symbol}</div><b style={{ color: col, fontSize: 17 }}>{r.level} risk ({r.score}/100)</b></div>
      </div>
      <div className="row gap wrap small">
        <span>Volatility</span><div style={{ width: 120 }}><Bar value={r.volatility_pct} color="var(--warn)" /></div><span style={{ color: "var(--warn)" }}>{r.volatility_pct ?? "—"}%</span>
        <span className="chip">Spread: {r.spread == null ? "—" : t.asset === "forex" ? `${(r.spread / (t.symbol.endsWith("JPY") ? 0.01 : 0.0001)).toFixed(1)} pips` : Number(r.spread).toFixed(t.digits)}</span>
        <span className="chip">Liquidity: <b className={r.liquidity === "High" ? "pos" : r.liquidity === "Low" ? "neg" : ""}>{r.liquidity}</b></span>
        <span className="muted" title={r.parts.map((p) => `${p.factor}: ${p.detail}`).join("\n")}>ⓘ why</span>
      </div>
    </div>
  );
}

function pipOf(symbol, asset) {
  if (asset !== "forex") return null;
  return symbol.endsWith("JPY") ? 0.01 : 0.0001;
}

function OrderPanel({ symbol, t, quote, onPlaced }) {
  const { can } = useAuth();
  const toast = useToast();
  const [run, busy] = useAction();
  const crypto = t?.venue === "crypto";
  const asset = t?.asset;
  const pip = pipOf(symbol, asset);
  const digits = t?.digits ?? 5;
  const [side, setSide] = useState("BUY");
  const [unitMode, setUnitMode] = useState("dist"); // dist | price
  const [f, setF] = useState({ sl: "", tp: "", risk: "", lots: "", sizing: "risk_amount" });
  const [pv, setPv] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { setF({ sl: "", tp: "", risk: "", lots: "", sizing: "risk_amount" }); setPv(null); if (crypto) setSide("BUY"); }, [symbol]); // eslint-disable-line
  const ref = side === "BUY" ? quote?.ask : quote?.bid;
  const distUnit = pip ? "pips" : "%";
  const toPrice = (v, dir) => {
    if (v === "" || v == null || !ref) return null;
    const n = Number(v);
    if (unitMode === "price") return n;
    const d = pip ? n * pip : (ref * n) / 100;
    return Number((dir > 0 ? ref + d : ref - d).toFixed(digits));
  };
  const slPx = toPrice(f.sl, side === "BUY" ? -1 : 1);
  const tpPx = toPrice(f.tp, side === "BUY" ? 1 : -1);
  const body = () => crypto
    ? { symbol, stop_loss: slPx, take_profit: tpPx, sizing_mode: f.sizing, sizing_value: f.sizing === "lots" ? Number(f.lots) : f.sizing === "risk_amount" ? Number(f.risk) || null : null }
    : { symbol, side, order_type: "MARKET", stop_loss: slPx, take_profit: tpPx, sizing_mode: f.sizing, sizing_value: f.sizing === "lots" ? Number(f.lots) : f.sizing === "risk_amount" ? Number(f.risk) || null : null };
  const key = JSON.stringify([symbol, side, slPx, tpPx, f.sizing, f.risk, f.lots]);
  useEffect(() => {
    setPv(null); setErr("");
    if (!slPx || (f.sizing === "risk_amount" && !f.risk) || (f.sizing === "lots" && !f.lots)) return undefined;
    const h = setTimeout(() => api.post(crypto ? "/crypto/preview" : "/live/preview", body()).then(setPv).catch((e) => setErr(e.message)), 400);
    return () => clearTimeout(h);
  }, [key]); // eslint-disable-line
  const suggest = () => api.get(crypto ? `/crypto/suggest?symbol=${symbol}` : `/live/suggest?symbol=${symbol}&side=${side}`).then((s) => {
    setUnitMode("price");
    setF((x) => ({ ...x, sl: String(s.stop_loss), tp: String(s.take_profit) }));
  }).catch((e) => setErr(e.message));
  const stepLots = (dir) => {
    const step = crypto ? (pv?.volume_min || 0.0001) : 0.01;
    const v = Math.max(0, (Number(f.lots) || 0) + dir * step);
    setF({ ...f, sizing: "lots", lots: String(Number(v.toFixed(8))) });
  };
  const place = () => run(() => api.post(crypto ? "/crypto/order" : "/live/order", body())).then((r) => {
    toast(crypto ? `Bought ${r.volume} ${symbol} @ ${r.price}${r.status === "CLOSED" ? ` — closed again: ${r.close_reason}` : ""}` : `Order sent: ${side} ${r.volume} ${symbol} (${r.status})`);
    onPlaced && onPlaced();
  }).catch(() => {});
  if (t?.venue === "watch") return <div className="tcard order-panel"><div className="tc-title">Watch only</div><p className="small">🇸🇦 Saudi stocks are for watching and research in this app: chart, AI explanation and price-alert lines work, orders do not.</p><p className="tiny muted">Prices from Yahoo Finance, may be delayed. To trade Saudi stocks use a CMA-licensed broker.</p></div>;
  if (!can("trading.manual")) return <div className="tcard order-panel"><div className="tc-title">New order</div><p className="muted small">Your account can view but not trade.</p></div>;
  return (
    <div className="tcard order-panel">
      <div className="tc-title row between"><span>New order</span><span className="chip info">{symbol}</span></div>
      {t?.route && (
        <div className={`route-banner r-${t.route.kind}`}>
          <div>Orders go to: <b>{t.route.label}</b></div>
          <div className="tiny">Auto-trade on this account: <b>{t.route.auto ? "ON ⚡ — qualified setups are placed by the app" : "OFF — setups wait for your approval"}</b> · <a href="#/lab">change</a></div>
        </div>
      )}
      <div className="bs">
        <button className={`bs-buy ${side === "BUY" ? "on" : ""}`} onClick={() => setSide("BUY")}>⊕ Buy<small>{quote ? Number(quote.ask).toFixed(digits) : ""}</small></button>
        <button className={`bs-sell ${side === "SELL" ? "on" : ""}`} disabled={crypto} title={crypto ? "Spot crypto: you can only buy, then sell what you own" : ""} onClick={() => setSide("SELL")}>⊖ Sell<small>{quote ? Number(quote.bid).toFixed(digits) : ""}</small></button>
      </div>
      {crypto && <div className="tiny muted">Spot crypto = buy only (no borrowing). You sell by closing a coin you own. To trade falling prices use the broker's BTCUSD (CFD) on demo.</div>}
      <label className="olbl">Size</label>
      <div className="seg full">
        <button className={f.sizing === "risk_amount" ? "active" : ""} onClick={() => setF({ ...f, sizing: "risk_amount" })}>Max loss</button>
        <button className={f.sizing === "auto" ? "active" : ""} onClick={() => setF({ ...f, sizing: "auto" })}>Auto</button>
        <button className={f.sizing === "lots" ? "active" : ""} onClick={() => setF({ ...f, sizing: "lots" })}>{crypto ? "Coins" : asset === "forex" ? "Lots" : "Units"}</button>
      </div>
      {f.sizing === "risk_amount" && <input type="number" min="0" step="0.5" placeholder={`max loss in ${crypto ? "USDT" : "$"} if the stop hits`} value={f.risk} onChange={(e) => setF({ ...f, risk: e.target.value })} />}
      {f.sizing === "lots" && (
        <div className="row gap"><input type="number" step="any" value={f.lots} onChange={(e) => setF({ ...f, lots: e.target.value })} />
          <button className="btn sm" onClick={() => stepLots(1)}>+</button><button className="btn sm" onClick={() => stepLots(-1)}>−</button></div>
      )}
      {f.sizing === "auto" && <div className="small muted">Risk per trade from Risk & Strategy (default 0.5% of the account).</div>}
      <div className="row between"><label className="olbl">Stop loss (SL) — required</label>
        <button className="chip" onClick={() => { setUnitMode(unitMode === "price" ? "dist" : "price"); setF({ ...f, sl: "", tp: "" }); }}>{unitMode === "price" ? "PRICE" : distUnit.toUpperCase()} ⇄</button></div>
      <input type="number" step="any" placeholder={unitMode === "price" ? "price" : `distance in ${distUnit}`} value={f.sl} onChange={(e) => setF({ ...f, sl: e.target.value })} />
      {unitMode !== "price" && slPx && <div className="tiny muted">= price {slPx}</div>}
      <label className="olbl">Take profit (TP)</label>
      <input type="number" step="any" placeholder={unitMode === "price" ? "price" : `distance in ${distUnit}`} value={f.tp} onChange={(e) => setF({ ...f, tp: e.target.value })} />
      {unitMode !== "price" && tpPx && <div className="tiny muted">= price {tpPx}</div>}
      <button className="btn sm ghost" style={{ marginTop: 6 }} onClick={suggest}>↺ Suggest SL/TP from volatility</button>
      <div className="pl2">
        <div><span>P/L if SL</span><b className="neg">{pv?.loss_if_sl != null ? `−${fmt.money(pv.loss_if_sl)}` : "—"}</b></div>
        <div><span>P/L if TP</span><b className="pos">{pv?.gain_if_tp != null ? `+${fmt.money(pv.gain_if_tp)}` : "—"}</b></div>
      </div>
      {pv && <div className="tiny muted">Size {pv.volume} {crypto ? symbol.replace(/USDT|USDC$/, "") : "lots"}{pv.notional ? ` (≈ ${fmt.money(pv.notional)} USDT)` : ""} · R:R {pv.reward_risk ?? "—"} · limit {fmt.money(pv.risk_budget)}</div>}
      {pv && !pv.approved && <div className="alert bad tiny">{pv.checks.filter((c) => !c.passed).map((c) => `✕ ${c.check}${c.detail ? ` (${c.detail})` : ""}`).join("  ·  ")}</div>}
      {err && <div className="alert bad tiny">{err}</div>}
      <button className={`btn block ${side === "BUY" ? "buy" : "sell"}`} disabled={busy || !pv?.approved} onClick={place}>
        {pv?.approved ? `Place ${side} ${pv.volume}` : "Enter stop-loss & size"}
      </button>
      <div className="tiny muted">Every order passes the risk engine: stop-loss required, size capped, loss limits, kill switch.{crypto ? " A stop-loss order is placed on the exchange right after the buy." : ""}</div>
    </div>
  );
}

export default function Terminal() {
  const toast = useToast();
  const [symbol, setSymbol] = useState(getSym() || "EURUSD");
  const [brief, setBrief] = useState(null);
  const { data: wl, reload: reloadWl } = useApi("/intel/watchlist", { interval: 5000 });
  const { data: t, reload: reloadT } = useApi(`/intel/terminal?symbol=${symbol}`, { interval: 10000, deps: [symbol] });
  const [tf, setTfRaw] = useState(() => { try { return localStorage.getItem("q.tf") || "H1"; } catch { return "H1"; } });
  const setTf = (v) => { setTfRaw(v); try { localStorage.setItem("q.tf", v); } catch { /* ignore */ } };
  const [ind, toggleInd] = useIndicators();
  const { data: cd } = useApi(`/intel/candles?symbol=${symbol}&tf=${tf}&limit=60`, { interval: 30000, deps: [symbol, tf] });
  const { data: live, reload: reloadLive } = useApi("/live/feed", { interval: 5000 });
  const { data: cf, reload: reloadCf } = useApi("/crypto/feed", { interval: 5000 });
  const { can } = useAuth();
  const [run] = useAction();
  useEffect(() => {
    if (wl?.length && !wl.some((w) => w.symbol === symbol)) setSymbol(wl[0].symbol);
  }, [wl]); // eslint-disable-line
  const pick = (s) => { setSymbol(s); window.history.replaceState(null, "", `#/terminal?s=${s}`); };
  const w = (wl || []).find((x) => x.symbol === symbol);
  const crypto = t?.venue === "crypto";
  const positions = crypto ? (cf?.positions || []) : (live?.positions || []);
  const mine = positions.filter((p) => p.symbol === symbol);
  const levels = mine.flatMap((p) => [{ price: p.open_price, label: "entry", color: "#e2e8f0" }, { price: p.stop_loss, label: "SL", color: "var(--bad)" }, { price: p.take_profit, label: "TP", color: "var(--ok)" }]);
  const c = cd?.candles || [];
  const last = c[c.length - 1], first = c[0];
  const chg = last && first ? ((last.close - first.open) / first.open) * 100 : null;
  const digits = t?.digits ?? w?.digits ?? 5;
  const fav = (s) => {
    const cur = (wl || []).filter((x) => x.favorite).map((x) => x.symbol);
    api.put("/intel/favorites", { symbols: cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s] }).then(reloadWl);
  };
  const doBrief = () => {
    setBrief({ loading: true });
    api.post("/intel/brief", { symbol }).then((r) => setBrief(r)).catch((e) => { setBrief(null); toast(e.message, "err"); });
  };
  const refresh = () => { reloadT(); reloadLive(); reloadCf(); };
  return (
    <div className="terminal">
      <TopStats t={t} symbol={symbol} />
      <div className="term-body">
        <Watchlist items={wl} symbol={symbol} onPick={pick} onFav={fav} />
        <div className="term-main">
          <div className="tcards">
            <Highlights t={t} />
            <Sentiment t={t} />
            <Insights t={t} onBrief={can("ai.use") ? doBrief : null} />
            <Achievements t={t} />
          </div>
          <div className="term-grid">
            <div className="term-center">
              <RiskStrip t={t} />
              <div className="tcard">
                <div className="row between wrap">
                  <div>
                    <div className="row gap"><b>{symbol}</b>{chg != null && <span className={`small ${pnlClass(chg)}`}>{chg > 0 ? "↗" : "↘"} {chg.toFixed(2)}%</span>}</div>
                    <div className="big-px mono">{w?.bid != null ? Number(w.bid).toFixed(digits) : last ? last.close.toFixed(digits) : "—"}</div>
                    {last && <div className="tiny muted mono">H {Math.max(...c.map((x) => x.high)).toFixed(digits)} | L {Math.min(...c.map((x) => x.low)).toFixed(digits)} | O {first.open.toFixed(digits)}</div>}
                  </div>
                  <ExplainChart symbol={symbol} tf={tf} />
                  <div className="tf-row scroll-x">{TFS.map(([k, l]) => <button key={k} className={tf === k ? "on" : ""} onClick={() => setTf(k)}>{l}</button>)}</div>
                </div>
                <ChartPanel symbol={symbol} tf={tf} digits={digits} levels={levels} ind={ind} toggleInd={toggleInd} />
              </div>
              <div className="tcard">
                <div className="tc-title">Open positions · {t?.account?.mode_label || ""}</div>
                {positions.length === 0 ? <p className="muted small">No open positions.</p> : (
                  <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Market</th><th>Side</th><th className="num">Size</th><th className="num">Entry</th><th className="num">SL</th><th className="num">TP</th><th className="num">P/L</th><th /></tr></thead>
                    <tbody>{positions.map((p) => { const dg = (wl || []).find((x) => x.symbol === p.symbol)?.digits ?? 5; const px = (v) => (v == null ? "—" : Number(v).toFixed(dg)); return (
                      <tr key={p.id} className={p.symbol === symbol ? "hl-row" : ""} onClick={() => pick(p.symbol)}>
                        <td>{p.symbol}</td><td className={p.side === "BUY" ? "pos" : "neg"}>{p.side}</td><td className="num">{p.volume}</td>
                        <td className="num mono">{px(p.open_price)}</td><td className="num mono">{px(p.stop_loss)}</td><td className="num mono">{px(p.take_profit)}</td>
                        <td className={`num ${pnlClass(p.unrealized_pnl)}`}>{fmt.money(p.unrealized_pnl)}</td>
                        <td>{can("killswitch.use") && <button className="btn sm" onClick={(e) => { e.stopPropagation(); if (window.confirm(`Close ${p.symbol}?`)) run(() => api.post(`/positions/${p.id}/close`), "Close requested").then(refresh); }}>Close</button>}</td>
                      </tr>); })}</tbody></table></div>
                )}
                <a className="small" href="#/live">Full trade management (edit stop, journal, screenshots) →</a>
              </div>
            </div>
            <OrderPanel symbol={symbol} t={t} quote={w} onPlaced={refresh} />
          </div>
        </div>
      </div>
      {brief && (
        <Modal title={`AI market brief · ${symbol}`} onClose={() => setBrief(null)} wide>
          {brief.loading ? <p className="muted">Asking the AI to explain the app's data…</p> : <div className="brief">{brief.brief}</div>}
          <p className="tiny muted">The AI only explains the app's measured data. It cannot place trades and does not predict prices.</p>
        </Modal>
      )}
    </div>
  );
}
