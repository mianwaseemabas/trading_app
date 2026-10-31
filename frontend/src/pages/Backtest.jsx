import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Badge, Card, Field, LineChart, Modal, Table, digitsFor, fmt, pnlClass, statusTone, useAction, useApi } from "../components.jsx";
import { MetricsGrid } from "./Trades.jsx";

function ImportCsv({ onClose, symbols }) {
  const [run, busy] = useAction();
  const [symbol, setSymbol] = useState(symbols[0] || "EURUSD");
  const [tf, setTf] = useState("H1");
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  return (
    <Modal title="Import historical candles (CSV)" onClose={onClose}>
      <p className="muted small">In MT5: View → Symbols → Bars → choose symbol & timeframe → Request → Export Bars. Files with columns DATE, TIME, OPEN, HIGH, LOW, CLOSE (tab or comma separated) are accepted. Times are taken as UTC — export with your broker's server time aligned to UTC where possible.</p>
      <div className="grid two">
        <Field label="Symbol"><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></Field>
        <Field label="Timeframe"><select value={tf} onChange={(e) => setTf(e.target.value)}>{["M15", "M30", "H1", "H4", "D1"].map((t) => <option key={t}>{t}</option>)}</select></Field>
      </div>
      <Field label="CSV file"><input type="file" accept=".csv,.txt" onChange={(e) => setFile(e.target.files[0])} /></Field>
      {result && <div className={`alert ${result.rejected ? "warn" : "ok"}`}>Stored {result.stored} candles, rejected {result.rejected}.{result.errors?.length ? " First errors: " + result.errors.slice(0, 3).join("; ") : ""}</div>}
      <div className="row end gap">
        <button className="btn ghost" onClick={onClose}>Close</button>
        <button className="btn primary" disabled={!file || busy} onClick={() => {
          const fd = new FormData();
          fd.append("symbol", symbol); fd.append("timeframe", tf); fd.append("file", file);
          run(() => api.upload("/market/import-csv", fd), "Import finished").then(setResult).catch(() => {});
        }}>{busy ? "Importing…" : "Import"}</button>
      </div>
    </Modal>
  );
}

const STRAT_LABEL = { trend_pullback: "EMA trend pullback (baseline)", smc_sweep_bos: "SMC sweep → BOS → retest" };

function SegmentsTable({ segs }) {
  const rows = ["development", "validation", "final_test"].filter((k) => segs[k]).map((k) => ({ id: k, k, ...segs[k] }));
  return (
    <Table rows={rows} cols={[
      { label: "Period", render: (x) => ({ development: "Development (60%)", validation: "Validation (20%)", final_test: "Final test (20%)" })[x.k] },
      { label: "From", render: (x) => fmt.dt(x.start) }, { label: "To", render: (x) => fmt.dt(x.end) },
      { label: "Trades", k: "trades", num: true }, { label: "Win %", num: true, render: (x) => fmt.pct(x.win_rate_pct, 1) },
      { label: "PF", num: true, render: (x) => fmt.num(x.profit_factor, 2) }, { label: "Avg R", num: true, render: (x) => fmt.num(x.avg_r, 2) },
      { label: "Expectancy", num: true, render: (x) => fmt.money(x.expectancy) },
      { label: "Net", num: true, render: (x) => <span className={pnlClass(x.net_pnl)}>{fmt.money(x.net_pnl)}</span> },
    ]} />
  );
}

function RunDetail({ id }) {
  const { data: r, reload } = useApi(`/backtests/${id}`, { deps: [id] });
  useEffect(() => {
    if (r?.status === "RUNNING") {
      const t = setTimeout(reload, 2000);
      return () => clearTimeout(t);
    }
  }, [r, reload]);
  if (!r) return <p className="muted">Loading…</p>;
  if (r.status === "RUNNING") return <p className="muted">Running…</p>;
  if (r.status === "FAILED") return <div className="alert bad">{r.error}</div>;
  const m = r.metrics;
  const d = digitsFor(r.params.symbol);
  return (
    <div className="stack">
      {m.warnings?.length ? (
        <div className="alert warn"><strong>Not robust:</strong><ul>{m.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
      ) : (
        <div className="alert ok">No robustness warnings on this sample. Past results do not establish future profitability — continue to demo testing.</div>
      )}
      <div className="row end"><a className="btn sm ghost" href={`#/assistant?q=${encodeURIComponent(`Explain my backtest #${id} (${r.params.symbol} ${r.params.timeframe}): all=${JSON.stringify(m.all)}, strategy=${m.strategy?.name}, segments(dev/val/final)=${JSON.stringify(m.segments)}, comparison=${JSON.stringify(m.comparison && { strategy: m.comparison.strategy, all: m.comparison.all })}, warnings=${JSON.stringify(m.warnings)}, rejected=${JSON.stringify(m.risk_rejections)}. Is it good enough for demo trading and what should I try next?`)}`}>✦ Ask AI to explain</a></div>
      <p className="muted small">{r.params.symbol} {r.params.timeframe} · {fmt.dt(m.period_start)} → {fmt.dt(m.period_end)} · {m.candles} candles · {m.signals} signals · strategy v{m.strategy?.version}</p>
      <p className="small">Strategy: <b>{STRAT_LABEL[m.strategy?.name] || m.strategy?.name}</b></p>
      {m.segments && Object.keys(m.segments).length > 0 && (
        <>
          <h4>Development / validation / final test (chronological)</h4>
          <SegmentsTable segs={m.segments} />
          <p className="muted small">Tune settings only by looking at <b>development</b>. Check <b>validation</b> occasionally. Look at the <b>final test</b> rarely — every time you change rules after seeing it, it stops being an honest test.</p>
        </>
      )}
      {m.comparison && (
        <>
          <h4>Comparison on the same data</h4>
          <Table rows={[{ id: 1, name: m.strategy?.name, all: m.all, ft: m.segments?.final_test, signals: m.signals },
            { id: 2, name: m.comparison.strategy, all: m.comparison.all, ft: m.comparison.segments?.final_test, signals: m.comparison.signals }]} cols={[
            { label: "Strategy", render: (x) => STRAT_LABEL[x.name] || x.name },
            { label: "Signals", k: "signals", num: true },
            { label: "Trades", num: true, render: (x) => x.all.trades },
            { label: "Win %", num: true, render: (x) => fmt.pct(x.all.win_rate_pct, 1) },
            { label: "PF", num: true, render: (x) => fmt.num(x.all.profit_factor, 2) },
            { label: "Avg R", num: true, render: (x) => fmt.num(x.all.avg_r, 2) },
            { label: "Max DD", num: true, render: (x) => fmt.money(x.all.max_drawdown) },
            { label: "Net", num: true, render: (x) => <span className={pnlClass(x.all.net_pnl)}>{fmt.money(x.all.net_pnl)}</span> },
            { label: "Final test net", num: true, render: (x) => <span className={pnlClass(x.ft?.net_pnl)}>{fmt.money(x.ft?.net_pnl)}</span> },
          ]} />
          <p className="muted small">Does the SMC logic add measurable value over the simple EMA baseline? Only trust a difference that holds in the final test and over 30+ trades.</p>
        </>
      )}
      <h4>All trades</h4>
      <MetricsGrid m={m.all} />
      <LineChart series={[{ points: (r.equity_curve || []).map((p) => ({ x: new Date(p.time).getTime() / 1000, y: p.equity })) }]} baseline={r.params.initial_balance} yFormat={(v) => fmt.money(v)} />
      <div className="grid two">
        <div><h4>In-sample (first {Math.round((1 - r.params.oos_fraction) * 100)}%)</h4><MetricsGrid m={m.in_sample} /></div>
        <div><h4>Out-of-sample (last {Math.round(r.params.oos_fraction * 100)}%)</h4><MetricsGrid m={m.out_of_sample} /></div>
      </div>
      <h4>Segment stability (walk-forward segments)</h4>
      <Table rows={r.folds} rowKey="fold" cols={[
        { label: "Segment", k: "fold" }, { label: "From", render: (f) => fmt.dt(f.start) }, { label: "To", render: (f) => fmt.dt(f.end) },
        { label: "Trades", k: "trades", num: true }, { label: "Win %", num: true, render: (f) => fmt.pct(f.win_rate_pct, 1) },
        { label: "PF", num: true, render: (f) => fmt.num(f.profit_factor, 2) },
        { label: "Net", num: true, render: (f) => <span className={pnlClass(f.net_pnl)}>{fmt.money(f.net_pnl)}</span> },
        { label: "Max DD", num: true, render: (f) => fmt.money(f.max_drawdown) },
      ]} />
      {Object.keys(m.risk_rejections || {}).length > 0 && (
        <><h4>Signals rejected by the risk engine</h4><ul>{Object.entries(m.risk_rejections).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul></>
      )}
      <h4>Assumptions</h4>
      <div className="kv">{Object.entries(m.assumptions || {}).map(([k, v]) => <div key={k}><span className="muted">{k}</span><span>{String(v)}</span></div>)}</div>
      <h4>Trades</h4>
      <Table rows={r.trades} rowKey="entry_time" cols={[
        { label: "Entry", render: (t) => fmt.dt(t.entry_time) }, { label: "Exit", render: (t) => fmt.dt(t.exit_time) },
        { label: "Side", render: (t) => <Badge tone={statusTone(t.side)}>{t.side}</Badge> }, { label: "Lots", k: "volume", num: true },
        { label: "Entry px", num: true, render: (t) => fmt.px(t.entry, d) }, { label: "Exit px", num: true, render: (t) => fmt.px(t.exit, d) },
        { label: "Reason", k: "exit_reason" }, { label: "Costs", num: true, render: (t) => fmt.money(t.commission - t.swap) },
        { label: "R", num: true, render: (t) => fmt.num(t.r_multiple, 2) },
        { label: "Net", num: true, render: (t) => <span className={pnlClass(t.net_pnl)}>{fmt.money(t.net_pnl)}</span> },
      ]} />
    </div>
  );
}

export default function Backtest() {
  const { data: strat } = useApi("/system/strategy");
  const { data: runs, reload } = useApi("/backtests", { interval: 5000 });
  const [run, busy] = useAction();
  const [showImport, setShowImport] = useState(false);
  const [selected, setSelected] = useState(null);
  const [f, setF] = useState({ symbol: "EURUSD", timeframe: "H1", start: "", end: "", initial_balance: 500, spread_points: 12, slippage_points: 3, commission_per_lot: 7, swap_per_lot_per_night: -1, oos_fraction: 0.3, folds: 4, strategy: "", compare_baseline: true });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const { data: riskCfg } = useApi("/system/risk");
  useEffect(() => {
    // Default the backtest commission to the live risk setting (0 for Capital.com, which charges no commission).
    if (riskCfg?.risk) setF((x) => ({ ...x, commission_per_lot: riskCfg.risk.commission_per_lot }));
  }, [riskCfg]);
  const submit = () => {
    const body = { ...f, start: f.start || null, end: f.end || null, strategy: f.strategy || null };
    ["initial_balance", "spread_points", "slippage_points", "commission_per_lot", "swap_per_lot_per_night", "oos_fraction"].forEach((k) => (body[k] = Number(body[k])));
    body.folds = parseInt(body.folds, 10);
    run(() => api.post("/backtests", body), "Backtest started").then((r) => { setSelected(r.id); reload(); }).catch(() => {});
  };
  return (
    <div className="stack">
      <Card title="New backtest" actions={<button className="btn sm" onClick={() => setShowImport(true)}>Import CSV history</button>}>
        <p className="muted small">Uses your strategy settings (v{strat?.version}) and risk settings, the same code as live trading. Entries at the next candle open, conservative same-bar stop assumption, costs included.</p>
        <div className="grid form">
          <Field label="Strategy"><select value={f.strategy} onChange={set("strategy")}>
            <option value="">Live strategy ({STRAT_LABEL[strat?.name] || strat?.name})</option>
            {Object.entries(STRAT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></Field>
          <Field label="Compare"><label className="check"><input type="checkbox" checked={f.compare_baseline} onChange={(e) => setF({ ...f, compare_baseline: e.target.checked })} /> also run the other strategy on the same data</label></Field>
          <Field label="Symbol"><select value={f.symbol} onChange={set("symbol")}>{(strat?.symbols || ["EURUSD"]).map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Timeframe"><select value={f.timeframe} onChange={set("timeframe")}>{["M15", "H1", "H4"].map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="From (optional)"><input type="date" value={f.start} onChange={set("start")} /></Field>
          <Field label="To (optional)"><input type="date" value={f.end} onChange={set("end")} /></Field>
          <Field label="Initial balance"><input type="number" value={f.initial_balance} onChange={set("initial_balance")} /></Field>
          <Field label="Spread (points)" hint="used when candles carry no spread"><input type="number" value={f.spread_points} onChange={set("spread_points")} /></Field>
          <Field label="Slippage (points)"><input type="number" value={f.slippage_points} onChange={set("slippage_points")} /></Field>
          <Field label="Commission / lot (round turn)"><input type="number" value={f.commission_per_lot} onChange={set("commission_per_lot")} /></Field>
          <Field label="Swap / lot / night" hint="negative = cost"><input type="number" value={f.swap_per_lot_per_night} onChange={set("swap_per_lot_per_night")} /></Field>
          <Field label="Out-of-sample fraction"><input type="number" step="0.05" value={f.oos_fraction} onChange={set("oos_fraction")} /></Field>
          <Field label="Segments"><input type="number" value={f.folds} onChange={set("folds")} /></Field>
        </div>
        <div className="row end"><button className="btn primary" disabled={busy} onClick={submit}>Run backtest</button></div>
      </Card>
      <Card title="Runs">
        <Table rows={runs} onRow={(r) => setSelected(r.id)} cols={[
          { label: "#", k: "id" }, { label: "Started", render: (r) => fmt.dt(r.created_at) }, { label: "By", k: "created_by" },
          { label: "Market", render: (r) => `${r.params.symbol} ${r.params.timeframe}` },
          { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
          { label: "Trades", num: true, render: (r) => r.summary?.trades ?? "—" },
          { label: "Net", num: true, render: (r) => <span className={pnlClass(r.summary?.net_pnl)}>{fmt.money(r.summary?.net_pnl)}</span> },
          { label: "PF", num: true, render: (r) => fmt.num(r.summary?.profit_factor, 2) },
          { label: "Robust", render: (r) => (r.status === "DONE" ? <Badge tone={r.robust ? "ok" : "warn"}>{r.robust ? "no warnings" : "warnings"}</Badge> : "") },
        ]} />
      </Card>
      {selected && <Modal title={`Backtest #${selected}`} onClose={() => setSelected(null)} wide><RunDetail id={selected} /></Modal>}
      {showImport && <ImportCsv symbols={strat?.symbols || []} onClose={() => setShowImport(false)} />}
    </div>
  );
}
