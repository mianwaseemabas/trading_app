import React, { useState } from "react";
import { Tabs } from "../components.jsx";
import { Badge, Card, Modal, Table, digitsFor, fmt, statusTone, useApi } from "../components.jsx";
import { SetupReport } from "./Setups.jsx";
import { AssessmentPanel, VERDICT } from "./Assessment.jsx";

function CheckList({ items }) {
  if (!items || !items.length) return <p className="muted">None</p>;
  return (
    <ul className="checks">
      {items.map((c, i) => (
        <li key={i} className={c.passed ? "ok" : "bad"}>
          <span>{c.passed ? "✓" : "✕"}</span> {c.side ? <b>{c.side}: </b> : null}{c.check}{c.detail ? <span className="muted"> — {c.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function SignalDetail({ id, onClose }) {
  const { data: s } = useApi(`/signals/${id}`);
  return (
    <Modal title={`Signal #${id}`} onClose={onClose} wide>
      {!s ? <p className="muted">Loading…</p> : (
        <div className="stack">
          <div className="row gap wrap">
            <Badge tone={statusTone(s.action)}>{s.action}</Badge>
            <Badge tone={statusTone(s.status)}>{s.status}</Badge>
            <span>{s.symbol} {s.timeframe} · candle {fmt.dt(s.candle_time)} · {s.strategy} v{s.strategy_version} · mode {s.trading_mode}</span>
            <a className="btn sm ghost" href={`#/assistant?q=${encodeURIComponent(`Explain signal #${s.id}: ${s.symbol} ${s.timeframe} ${s.action}, status ${s.status}. Conditions: ${JSON.stringify(s.reasons)}. Risk checks: ${JSON.stringify((s.risk_checks || []).filter((c) => !c.passed))}. Explain in simple words what happened and what I can learn.`)}`} onClick={onClose}>✦ Ask AI to explain</a>
          </div>
          {s.action !== "NONE" && <AssessmentPanel signalId={s.id} />}
          {s.action !== "NONE" && (
            <div className="grid stats">
              <div className="stat"><div className="stat-l">Entry ref</div><div className="stat-v">{fmt.px(s.entry_ref, digitsFor(s.symbol))}</div></div>
              <div className="stat"><div className="stat-l">Stop-loss</div><div className="stat-v">{fmt.px(s.stop_loss, digitsFor(s.symbol))}</div></div>
              <div className="stat"><div className="stat-l">Take-profit</div><div className="stat-v">{fmt.px(s.take_profit, digitsFor(s.symbol))}</div></div>
              {s.features?.risk && <div className="stat"><div className="stat-l">Size / risk</div><div className="stat-v">{s.features.risk.volume} lots</div><div className="stat-s">{fmt.money(s.features.risk.risk_amount)} of {fmt.money(s.features.risk.risk_budget)} budget</div></div>}
            </div>
          )}
          <div className="grid two">
            <div><h4>Strategy conditions</h4><CheckList items={s.reasons} /></div>
            <div><h4>Risk checks</h4>{s.risk_checks?.length ? <CheckList items={s.risk_checks} /> : <p className="muted">Not evaluated (no trade proposed or mode does not place orders).</p>}</div>
          </div>
          <h4>Inputs (features)</h4>
          <div className="kv">
            {Object.entries(s.features || {}).filter(([k]) => !["risk", "gaps"].includes(k)).map(([k, v]) => (
              <div key={k}><span className="muted">{k}</span><span>{typeof v === "number" ? (Math.abs(v) < 0.01 ? v.toExponential(3) : v.toFixed(5)) : String(v)}</span></div>
            ))}
          </div>
          {s.orders?.length > 0 && (
            <>
              <h4>Orders</h4>
              <Table rows={s.orders} cols={[
                { label: "Client ID", k: "client_id" }, { label: "Mode", k: "mode" }, { label: "Volume", k: "volume", num: true },
                { label: "Status", render: (o) => <Badge tone={statusTone(o.status)}>{o.status}</Badge> },
                { label: "Fill", num: true, render: (o) => fmt.px(o.fill_price, digitsFor(o.symbol)) }, { label: "Error", k: "error" },
              ]} />
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

const TABS = [["all", "All"], ["qualified", "Qualified"], ["watch", "Watch"], ["rejected", "Rejected / no trade"], ["expired", "Expired"], ["completed", "Completed"]];

export default function Signals() {
  const [tab, setTab] = useState("all");
  const [raw, setRaw] = useState(false);
  const { data } = useApi(`/signal-board?tab=${tab}`, { interval: 30000, deps: [tab] });
  const { data: rawData } = useApi(raw ? "/signals?limit=300" : null, { interval: 30000, deps: [raw] });
  const [open, setOpen] = useState(null);
  const [review, setReview] = useState(null);
  const today = data?.today || {};
  return (
    <div className="stack">
      <div className="alert info small">
        Every strategy signal is scored (0–100, a <b>quality</b> score — not a probability), checked against hard gates (data, costs, risk) and
        given a verdict: <b>NO TRADE</b>, <b>WATCH</b>, <b>PAPER TEST</b> or <b>RESEARCH SUPPORTED</b>. Rejected and expired signals are kept and
        followed afterwards, so the results are not biased. Today: {Object.entries(today).map(([k, v]) => `${k.toLowerCase().replace("_", " ")} ${v}`).join(" · ") || "no setups yet"}.
      </div>
      <Card title="Signal board" actions={<label className="check small"><input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} /> show raw log (every candle)</label>}>
        <Tabs value={tab} onChange={setTab} tabs={TABS.map(([k, label]) => ({ k, label }))} />
        {!raw ? (
          <Table rows={data?.signals} onRow={(r) => setOpen(r.id)} empty="No signals in this tab yet" cols={[
            { label: "Candle", render: (r) => fmt.dt(r.candle_time) },
            { label: "Setup", render: (r) => <span><Badge tone={statusTone(r.side)}>{r.side}</Badge> {r.symbol} {r.timeframe}</span> },
            { label: "Score", num: true, render: (r) => (r.score != null ? <b>{r.score}</b> : "—") },
            { label: "Verdict", render: (r) => (r.verdict ? <Badge tone={VERDICT[r.verdict]?.tone}>{VERDICT[r.verdict]?.label}</Badge> : <span className="muted small">not scored</span>) },
            { label: "Spread", num: true, render: (r) => (r.spread != null ? `${r.spread} ${r.spread_unit}` : "—") },
            { label: "Data age", num: true, render: (r) => (r.data_age_s != null ? `${Math.round(r.data_age_s)}s` : "—") },
            { label: "Entry / SL / TP", render: (r) => <span className="mono small">{fmt.px(r.entry, digitsFor(r.symbol))} / {fmt.px(r.stop, digitsFor(r.symbol))} / {fmt.px(r.target, digitsFor(r.symbol))}</span> },
            { label: "Net R:R", num: true, render: (r) => r.net_rr ?? "—" },
            { label: "Max loss", num: true, render: (r) => (r.max_loss != null ? fmt.money(r.max_loss) : "—") },
            { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
            { label: "Afterwards", render: (r) => (r.outcome ? <span className={r.outcome_r > 0 ? "pos" : r.outcome_r < 0 ? "neg" : ""}>{r.outcome}{r.outcome_r != null ? ` ${r.outcome_r} R` : ""}</span> : "—") },
            { label: "Reason", render: (r) => <span className="small muted trunc" title={r.reason}>{r.reason}</span> },
            { label: "", render: (r) => <button className="btn sm ghost" onClick={(e) => { e.stopPropagation(); setReview(r.id); }}>Report</button> },
          ]} />
        ) : (
          <Table rows={rawData} onRow={(r) => setOpen(r.id)} cols={[
            { label: "#", k: "id" }, { label: "Candle", render: (r) => fmt.dt(r.candle_time) }, { label: "Symbol", k: "symbol" }, { label: "TF", k: "timeframe" },
            { label: "Action", render: (r) => <Badge tone={statusTone(r.action)}>{r.action}</Badge> },
            { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> }, { label: "Version", k: "strategy_version" },
          ]} />
        )}
      </Card>
      {open && <SignalDetail id={open} onClose={() => setOpen(null)} />}
      {review && <SetupReport id={review} onClose={() => setReview(null)} />}
    </div>
  );
}
