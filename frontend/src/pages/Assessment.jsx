import React from "react";
import { Badge, Card, Table, fmt, useApi } from "../components.jsx";

export const VERDICT = {
  NO_TRADE: { tone: "bad", label: "NO TRADE", d: "Do nothing — invalid data, too costly, too risky or weak evidence." },
  WATCH: { tone: "muted", label: "WATCH", d: "Interesting but not enough confirmation. No action." },
  PAPER_TEST: { tone: "info", label: "PAPER TEST", d: "Passes the rules, but profitability is NOT validated. Practice only." },
  RESEARCH_SUPPORTED: { tone: "ok", label: "RESEARCH SUPPORTED", d: "Enough out-of-sample evidence for controlled paper evaluation. Not a promise." },
};
export const RELIABILITY = {
  INSUFFICIENT_EVIDENCE: "Insufficient evidence", RESEARCH_ONLY: "Research only (no positive edge yet)", PAPER_CANDIDATE: "Paper candidate", PAUSED: "Paused",
};

const money = (v) => (v == null ? "unavailable" : fmt.money(v));

export function AssessmentPanel({ signalId, live = false }) {
  const { data: a, error } = useApi(`/signals/${signalId}/assessment${live ? "?refresh=true" : ""}`, { deps: [signalId, live] });
  if (error) return <div className="alert warn small">{error}</div>;
  if (!a) return <p className="muted small">Scoring…</p>;
  const e = a.economics || {};
  const v = VERDICT[a.verdict] || {};
  const cs = e.comparable_strategy || {};
  const cy = e.comparable_symbol || {};
  return (
    <div className="stack">
      <div className={`alert ${v.tone === "bad" ? "bad" : v.tone === "ok" ? "ok" : v.tone === "info" ? "info" : "warn"}`}>
        <div className="row between wrap gap">
          <b style={{ fontSize: 16 }}>{v.label} · score {a.score}/100 <span className="small muted">({a.tier})</span></b>
          <span className="small">Reliability: <b>{RELIABILITY[a.reliability] || a.reliability}</b></span>
        </div>
        <div className="small">{v.d} {a.score_note}</div>
        {a.reasons?.length > 0 && <ul className="small" style={{ margin: "6px 0 0" }}>{a.reasons.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}</ul>}
      </div>

      <div className="grid two">
        <Card title="Planned loss (net of costs)">
          {e.available ? (
            <div className="kv small">
              <div><span>Quantity</span><b>{e.quantity} {e.unit} (min {e.min_qty}, step {e.qty_step})</b></div>
              <div><span>Entry ({e.entry_side_of_market}) → stop</span><b>{e.entry} → {e.stop}</b></div>
              <div><span>Gross loss at stop</span><b>{money(e.gross_stop)}</b></div>
              <div><span>+ commission + slippage allowance</span><b>{money(e.commission_round_trip)} + {money(e.slippage_allowance)}</b></div>
              <div><span>Estimated loss at stop</span><b className="neg">−{money(e.net_stop)} ({e.risk_pct_of_equity}% of account)</b></div>
              <div><span>Risk budget</span><b>{money(e.risk_budget)}</b></div>
              <div className="tiny muted">{e.warning} Spread already included in the entry price ({money(e.spread_cost_in_entry)}).</div>
            </div>
          ) : <p className="muted small">Unavailable: {e.reason}</p>}
        </Card>
        <Card title="Potential gain (net of costs)">
          {e.available ? (
            <div className="kv small">
              <div><span>Target</span><b>{e.target ?? "none"}</b></div>
              <div><span>Gross gain at target</span><b>{money(e.gross_target)}</b></div>
              <div><span>Estimated net gain at target</span><b className="pos">+{money(e.net_target)}</b></div>
              <div><span>Reward : risk (gross → net)</span><b>{e.gross_rr ?? "—"} → {e.net_rr ?? "—"}</b></div>
              <div><span>Break-even win rate needed</span><b>{e.break_even_win_rate_pct != null ? `${e.break_even_win_rate_pct}%` : "—"}</b></div>
              <div className="tiny muted">{e.do_nothing}</div>
            </div>
          ) : <p className="muted small">Unavailable</p>}
        </Card>
      </div>

      <Card title="Is it worth taking? Actual comparable results (shadow-tracked, net of estimated costs)">
        <Table rows={[{ id: 1, k: `This market (${cy.n ?? 0})`, ...cy }, { id: 2, k: `Whole strategy (${cs.n ?? 0})`, ...cs }]} cols={[
          { label: "Sample", k: "k" },
          { label: "Win rate", render: (x) => (x.n ? `${x.win_rate_pct}% (95% CI ${x.win_rate_ci95?.join("–")}%)` : "—") },
          { label: "p(win) used", render: (x) => <b>{x.p_win === "UNKNOWN" ? "UNKNOWN" : `${x.p_win}%`}</b> },
          { label: "Expectancy", render: (x) => (x.expectancy_r != null ? `${x.expectancy_r} R${x.expectancy_ci95 ? ` (CI ${x.expectancy_ci95.join(" to ")})` : ""}` : "—") },
          { label: "Profit factor", render: (x) => x.profit_factor ?? "—" },
        ]} />
        <p className="tiny muted">p(win) stays UNKNOWN until at least {cs.min_sample ?? 30} comparable signals are resolved. It is never taken from the score.</p>
      </Card>

      <div className="grid two">
        <Card title="Score breakdown (rules, not probability)">
          <Table rows={a.breakdown.map((b, i) => ({ ...b, id: i }))} cols={[
            { label: "Component", k: "component" }, { label: "Points", num: true, render: (b) => `${b.points}/${b.max}` },
            { label: "Evidence", render: (b) => <span className="small muted">{b.evidence}</span> },
          ]} />
        </Card>
        <Card title="Hard gates (any ✕ = NO TRADE)">
          <ul className="checks small">{a.gates.map((g, i) => <li key={i} className={g.passed ? "ok" : "bad"}><span>{g.passed ? "✓" : "✕"}</span> {g.gate} <span className="muted">{g.detail}</span></li>)}</ul>
          <p className="tiny muted">Data: {a.data?.provider} · {a.data?.session} · quote age {a.data?.quote_age_s ?? "n/a"} s · spread {a.data?.spread_pips != null ? `${a.data.spread_pips} pips` : a.data?.spread_bps != null ? `${a.data.spread_bps} bps` : "n/a"}</p>
        </Card>
      </div>

      <div className="grid two">
        <Card title="Lifecycle">
          <ul className="feed small">{(a.lifecycle || []).map((x, i) => <li key={i}><span className="muted">{fmt.dt(x.at)}</span><b>{x.state.replace(/_/g, " ")}</b><span>{x.detail}</span></li>)}
            {!a.lifecycle?.length && <li className="muted">No lifecycle recorded (older signal).</li>}</ul>
        </Card>
        <Card title="What happened afterwards">
          {a.outcome ? (
            <div className="kv small">
              <div><span>Outcome</span><b>{a.outcome.status}</b></div>
              <div><span>Result (gross / net of costs)</span><b>{a.outcome.r_gross ?? "—"} R / {a.outcome.r_net ?? "—"} R</b></div>
              <div><span>Best / worst excursion</span><b>+{a.outcome.mfe_r} R / −{a.outcome.mae_r} R</b></div>
              <div><span>Candles checked</span><b>{a.outcome.bars}</b></div>
              <div><span>Actually traded?</span><b>{a.outcome.traded ? "yes" : "no (shadow only)"}</b></div>
            </div>
          ) : <p className="muted small">Not resolved yet — checked every minute against later candles (stop assumed first if both are touched in one candle).</p>}
        </Card>
      </div>
    </div>
  );
}
