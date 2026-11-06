import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, LineChart, PasswordPrompt, Switch, fmt, useAction, useApi, useToast } from "../components.jsx";

const TM_TONE = { ABOVE_BREAK_EVEN: "good", UNCLEAR: "warn", BELOW_BREAK_EVEN: "bad", TOO_FEW: "muted", UNKNOWN: "muted" };
const TM_LABEL = { ABOVE_BREAK_EVEN: "above break-even", UNCLEAR: "no clear edge", BELOW_BREAK_EVEN: "below break-even", TOO_FEW: "too few past setups", UNKNOWN: "unknown" };
const STANCE_TONE = { AGREE: "good", NEUTRAL: "warn", OPPOSE: "bad", ERROR: "muted" };
const HOLD_TONE = { BEATS_HOLD: "good", BEATS_HOLD_RISK_ADJUSTED: "info", MORE_RETURN_MORE_PAIN: "warn", LOSES_TO_HOLD: "bad", NO_TRADES: "muted", NO_BENCHMARK: "muted" };
const HOLD_LABEL = { BEATS_HOLD: "beats hold", BEATS_HOLD_RISK_ADJUSTED: "beats hold (risk-adjusted)", MORE_RETURN_MORE_PAIN: "more return, more drawdown",
  LOSES_TO_HOLD: "loses to hold", NO_TRADES: "no trades", NO_BENCHMARK: "—" };
const COLORS = ["var(--accent)", "var(--ok)", "var(--warn)", "#b07cff", "#4fc3f7", "#ff8a65"];

export const HoldBadge = ({ v }) => (v ? <Badge tone={HOLD_TONE[v.code] || "muted"} title={v.text}>{HOLD_LABEL[v.code] || v.code}</Badge> : <span className="muted">—</span>);

// ------------------------------------------------------------------ Time Machine
function TimeMachineFull({ r }) {
  const rp = r.replay, sm = r.simulation || {};
  return (
    <div className="stack-xs">
      <div className="outcome">
        <div><div className="lbl">Break-even win rate</div><div className="val">{rp?.break_even_pct ?? r.break_even_pct ?? "—"}%</div>
          <div className="small muted">R:R {r.reward_risk} · costs {r.cost_r}R</div></div>
        <div><div className="lbl">Past setups: target first</div><div className="val">{rp ? `${rp.p_target_pct ?? "—"}%` : "—"}</div>
          <div className="small muted">{rp ? `${rp.target_first} of ${rp.n} · 95% ${rp.p_target_ci95 ? rp.p_target_ci95.join("–") : "—"}%` : "manual trade: no strategy history"}</div></div>
        <div><div className="lbl">Past setups: avg result</div><div className={`val ${rp?.avg_r_net > 0 ? "pos" : rp?.avg_r_net < 0 ? "neg" : ""}`}>{rp?.avg_r_net ?? "—"}R</div>
          <div className="small muted">after costs · median {rp?.median_bars ?? "—"} candles</div></div>
        <div><div className="lbl">Random-entry simulation</div><div className="val">{sm.paths ? `${sm.p_target_pct}%` : "—"}</div>
          <div className="small muted">{sm.paths ? `${sm.paths.toLocaleString()} paths · stop ${sm.stop_atr}×ATR · ${sm.p_expired_pct}% unresolved` : sm.note}</div></div>
      </div>
      <div className={`alert ${TM_TONE[r.verdict?.code] === "good" ? "ok" : TM_TONE[r.verdict?.code] === "bad" ? "bad" : "info"} small`}>{r.verdict?.text}</div>
      {rp?.note && <p className="tiny muted">{rp.note}</p>}
      {rp?.recent?.length > 0 && (
        <details className="small"><summary>Latest past setups ({rp.recent.length})</summary>
          <div className="tbl-wrap"><table className="tbl"><thead><tr><th>Signal candle</th><th>Result</th><th>R</th><th>Candles</th></tr></thead>
            <tbody>{rp.recent.map((x, i) => <tr key={i}><td>{fmt.dt(x.time)}</td><td>{x.result}</td><td className={x.r > 0 ? "pos" : "neg"}>{x.r}</td><td>{x.bars}</td></tr>)}</tbody></table></div>
        </details>
      )}
      <p className="tiny muted">{r.honesty}</p>
    </div>
  );
}

function AIReviewView({ rv }) {
  if (!rv) return null;
  if (rv.error) return <div className="alert bad small">AI review failed: {rv.error}{rv.action === "VETO" && " — the auto trade was sent to manual approval (fails closed)."}</div>;
  return (
    <div className="stack-xs">
      <div className="row gap wrap">
        <Badge tone={STANCE_TONE[rv.stance]}>{rv.rating}</Badge>
        <Badge tone={STANCE_TONE[rv.stance]}>{rv.stance === "AGREE" ? "agrees with the setup" : rv.stance === "OPPOSE" ? "against the setup" : "no clear edge"}</Badge>
        <span className="small">confidence {rv.confidence}%</span>
        {rv.action && rv.action !== "NONE" && <Badge tone={rv.action === "VETO" ? "bad" : rv.action === "HALF" ? "warn" : "good"}>auto trade: {rv.action === "HALF" ? "half size" : rv.action.toLowerCase()}</Badge>}
        <span className="tiny muted">{rv.mode} · {rv.style} · {rv.model || rv.provider} · {rv.calls} call{rv.calls === 1 ? "" : "s"} · {rv.seconds}s · {fmt.dt(rv.at)}</span>
      </div>
      <div className="grid two">
        <div className="helper-box small"><b className="pos">Strongest point FOR</b><div>{rv.bull_point || "—"}</div></div>
        <div className="helper-box small"><b className="neg">Strongest point AGAINST</b><div>{rv.bear_point || "—"}</div></div>
      </div>
      {rv.change_mind && <p className="small"><b>Would change its mind:</b> {rv.change_mind}</p>}
      {rv.summary && <p className="small muted" style={{ whiteSpace: "pre-line" }}>{rv.summary}</p>}
      {(rv.bull_case || rv.bear_case) && (
        <details className="small"><summary>Full bull and bear cases</summary>
          <div className="grid two"><pre className="alert-body">{rv.bull_case}</pre><pre className="alert-body">{rv.bear_case}</pre></div>
        </details>
      )}
    </div>
  );
}

/** Time Machine odds + AI desk for one setup (used in the setup review). */
export function InsightPanel({ signalId }) {
  const { can } = useAuth();
  const { data, reload } = useApi(`/insight/${signalId}`, { deps: [signalId], interval: 20000 });
  const [full, setFull] = useState(null);
  const [run, busy] = useAction();
  if (!data) return <p className="small muted">Loading…</p>;
  const t = data.time_machine;
  return (
    <div className="grid two">
      <Card title="⏳ Time Machine — target before stop?" actions={
        <button className="btn ghost sm" disabled={busy} onClick={() => run(() => api.post(`/insight/${signalId}/time-machine`)).then(setFull).catch(() => {})}>
          {full ? "Refresh" : "Full report"}</button>}>
        {full ? <TimeMachineFull r={full} /> : t ? (
          <div className="stack-xs">
            <div className="row gap wrap"><Badge tone={TM_TONE[t.verdict]}>{TM_LABEL[t.verdict] || t.verdict}</Badge>
              <span className="small">past: <b>{t.replay_p_target_pct ?? "—"}%</b> of {t.replay_n ?? 0} · random: {t.sim_p_target_pct ?? "—"}% · break-even {t.break_even_pct ?? "—"}%</span></div>
            <p className="small muted">{t.text}</p>
          </div>
        ) : <p className="small muted">Not computed for this setup yet — press “Full report”.</p>}
      </Card>
      <Card title="✦ AI desk — bull vs bear" actions={can("ai.use") && (
        <button className="btn ghost sm" disabled={busy} onClick={() => run(() => api.post(`/insight/${signalId}/ai-review`), "AI review done").then(reload).catch(() => {})}>
          {data.ai_review ? "Review again" : "Run AI review"}</button>)}>
        {data.ai_review ? <AIReviewView rv={data.ai_review} /> : (
          <p className="small muted">No AI review yet. {data.ai_mode === "OFF" ? "Automatic reviews are off (Strategy lab → AI desk)." : "Reviews run automatically for qualified setups."}
            {" "}A manual review is information only; it does not change an order.</p>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ settings card (Strategy lab / Auto-trade)
export function InsightSettingsCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/insight/settings", { interval: 60000 });
  const { data: stats } = useApi("/insight/stats/ai", { interval: 300000 });
  const [run, busy] = useAction();
  const toast = useToast();
  const [ask, setAsk] = useState(null);
  if (!data) return null;
  const edit = can("strategy.edit");
  const c = data.ai_review, tm = data.time_machine;
  const put = (body) => api.put("/insight/settings", body).then(reload);
  // a 403 means this change makes a check less strict: ask for the password, then retry
  const save = (body, msg) => put(body).then(() => toast(msg)).catch((e) => (e.status === 403 ? setAsk({ body, msg }) : toast(e.message, "err")));
  return (
    <Card title="AI desk & Time Machine (filters before an auto trade)">
      <p className="small muted">Two extra checks on qualified setups. Both can only <b>block or shrink</b> a trade — they never place or enlarge one, and the
        risk engine still checks every order. Making a check less strict asks for your password.</p>
      <div className="grid two">
        <div className="helper-box">
          <h4>⏳ Time Machine</h4>
          <div className="tiny muted">For every setup: how often did the same strategy's past setups on this market hit the target before the stop
            (one trade at a time, stop first if both hit in one candle), compared with the win rate needed to break even and with 10,000
            random-entry simulations.</div>
          <Switch on={tm.enabled} disabled={!edit || busy} label="Compute for every setup" onChange={(v) => save({ tm_enabled: v }, v ? "Time Machine ON" : "Time Machine OFF")} />
          <Switch on={tm.gate} disabled={!edit || busy} label="Block setups clearly below break-even (hard gate)"
            onChange={(v) => save({ tm_gate: v }, v ? "Time Machine gate ON" : "Time Machine gate OFF")} />
        </div>
        <div className="helper-box">
          <h4>✦ AI bull/bear review</h4>
          <div className="tiny muted">A bull analyst and a bear analyst argue from the app's data, then a risk manager rates the market
            BUY → SELL. <b>Log only</b> records the reviews so you can see if they help. <b>Veto</b> reviews before every auto trade:
            against → the setup waits for your approval; neutral → {c.neutral_action === "HALF" ? "half size" : c.neutral_action.toLowerCase()};
            AI error → your approval.{!data.ai_ready && " Set up the AI assistant first (AI Assistant page)."}</div>
          <div className="row gap wrap">
            <Field label="Mode">
              <select disabled={!edit || busy} value={c.mode} onChange={(e) => save({ mode: e.target.value }, `AI review: ${e.target.value}`)}>
                <option value="OFF">Off</option><option value="LOG_ONLY">Log only (start here)</option><option value="VETO" disabled={!data.ai_ready}>Veto auto trades</option>
              </select>
            </Field>
            <Field label="Style">
              <select disabled={!edit || busy} value={c.style} onChange={(e) => save({ style: e.target.value }, "Saved")}>
                <option value="debate">Debate (3 AI calls)</option><option value="quick">Quick (1 AI call)</option>
              </select>
            </Field>
            <Field label="When neutral">
              <select disabled={!edit || busy} value={c.neutral_action} onChange={(e) => save({ neutral_action: e.target.value }, "Saved")}>
                <option value="HALF">Half size</option><option value="VETO">Ask me</option><option value="ALLOW">Trade normally</option>
              </select>
            </Field>
            <Field label="Veto when against with ≥">
              <select disabled={!edit || busy} value={c.min_confidence} onChange={(e) => save({ min_confidence: Number(e.target.value) }, "Saved")}>
                {[40, 50, 60, 70, 80].map((v) => <option key={v} value={v}>{v}% confidence</option>)}
              </select>
            </Field>
            <Field label="Max reviews / day">
              <select disabled={!edit || busy} value={c.max_per_day} onChange={(e) => save({ max_per_day: Number(e.target.value) }, "Saved")}>
                {[5, 10, 20, 40, 100].map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </Field>
          </div>
          <div className="tiny muted">{data.reviews_left_today} reviews left today. Each review costs AI credit (debate ≈ 3 calls). Once the daily limit is used up, Veto mode sends auto trades to your approval.</div>
        </div>
      </div>
      {stats && (
        <div className="helper-box small" style={{ marginTop: 8 }}>
          <b>Is the AI helping?</b> {stats.verdict}
          <div className="row gap wrap tiny" style={{ marginTop: 4 }}>
            {Object.entries(stats.by_stance).map(([k, v]) => <span key={k}><Badge tone={STANCE_TONE[k]}>{k}</Badge> {v.n} resolved · win {v.win_rate_pct ?? "—"}% · avg {v.avg_r ?? "—"}R</span>)}
          </div>
        </div>
      )}
      {ask && (
        <PasswordPrompt title="Make this check less strict?" onClose={() => setAsk(null)}
          message="Turning a protective check off or making it weaker needs your password."
          onSubmit={(pw) => run(() => put({ ...ask.body, password: pw }), ask.msg)} />
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ compare strategies vs holding
export function CompareCard({ catalog, markets }) {
  const { data, reload } = useApi("/lab/compare", { interval: 4000 });
  const [run, busy] = useAction();
  const [sym, setSym] = useState(markets[0] || "EURUSD");
  const [tf, setTf] = useState("H1");
  const [picked, setPicked] = useState(["trend_pullback", "donchian_breakout", "supertrend_ema"]);
  const [fee, setFee] = useState("");
  if (!data) return null;
  const running = data.running || data.status === "RUNNING";
  const toggle = (n) => setPicked(picked.includes(n) ? picked.filter((x) => x !== n) : picked.length < data.max ? [...picked, n] : picked);
  const start = () => run(() => api.post("/lab/compare", { symbol: sym, timeframe: tf, strategies: picked, ...(fee !== "" ? { fee_pct: Number(fee) } : {}) }),
    "Comparison started").then(reload).catch(() => {});
  const toX = (p) => new Date(p.time).getTime() / 1000;
  const series = data.status === "DONE" ? [
    { points: (data.hold_curve || []).map((p) => ({ x: toX(p), y: p.pct })), color: "var(--line2)" },
    ...data.results.map((r, i) => ({ points: r.curve.map((p) => ({ x: toX(p), y: p.pct })), color: COLORS[i % COLORS.length] })),
  ] : [];
  return (
    <Card title="Compare strategies side by side — and against just holding" actions={
      <button className="btn primary sm" disabled={busy || running || !picked.length} onClick={start}>{running ? `Running… ${data.progress || ""}` : "Compare"}</button>}>
      <p className="small muted">Backtests up to {data.max} strategies on one market with the same costs, then compares each with simply buying at the start and
        holding to the end. A strategy is only worth its effort if it beats holding on <b>return per drawdown</b>, not just on return.</p>
      <div className="row gap wrap">
        <Field label="Market"><select value={sym} onChange={(e) => setSym(e.target.value)}>{markets.map((m) => <option key={m}>{m}</option>)}</select></Field>
        <Field label="Timeframe"><select value={tf} onChange={(e) => setTf(e.target.value)}>{["M15", "H1", "H4", "D1"].map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Crypto fee per side %" hint="blank = your exchange setting"><input style={{ width: 90 }} value={fee} placeholder="0.1" onChange={(e) => setFee(e.target.value)} /></Field>
      </div>
      <div className="row gap wrap small">{catalog.map((s) => (
        <label key={s.name} className="check"><input type="checkbox" checked={picked.includes(s.name)} onChange={() => toggle(s.name)} /> {s.label || s.name}</label>
      ))}</div>
      {data.status === "FAILED" && <div className="alert bad small">{data.error}</div>}
      {data.status === "DONE" && (
        <div className="stack-xs" style={{ marginTop: 8 }}>
          <div className="alert info small"><b>{data.symbol} {data.timeframe}</b> · {fmt.dt(data.period[0])} → {fmt.dt(data.period[1])} · {data.candles} candles. {data.summary}</div>
          <LineChart series={series} baseline={0} yFormat={(v) => `${fmt.num(v, 1)}%`} />
          <div className="row gap wrap tiny"><span><span style={{ color: "var(--line2)" }}>━</span> hold</span>
            {data.results.map((r, i) => <span key={r.strategy}><span style={{ color: COLORS[i % COLORS.length] }}>━</span> {r.strategy}</span>)}</div>
          <div className="tbl-wrap"><table className="tbl">
            <thead><tr><th>Strategy</th><th>Trades</th><th>Return %</th><th>Max DD %</th><th>Return / DD</th><th>Win %</th><th>PF</th><th>Final test PF</th><th>Costs</th><th>vs hold</th></tr></thead>
            <tbody>
              <tr className="muted"><td>Buy &amp; hold</td><td>1</td><td>{fmt.num(data.hold.return_pct, 2)}</td><td>{fmt.num(data.hold.max_drawdown_pct, 1)}</td>
                <td>{data.hold.return_per_dd ?? "—"}</td><td colSpan={5} className="tiny">bought {data.hold.entry}, sold {data.hold.exit} (no swap)</td></tr>
              {data.results.map((r) => (
                <tr key={r.strategy}>
                  <td><b className="small">{r.strategy}</b>{r.warnings?.length > 0 && <div className="tiny muted" title={r.warnings.join("\n")}>{r.warnings.length} warning{r.warnings.length > 1 ? "s" : ""}</div>}</td>
                  <td>{r.trades}</td><td className={r.return_pct > 0 ? "pos" : r.return_pct < 0 ? "neg" : ""}>{fmt.num(r.return_pct, 2)}</td>
                  <td>{fmt.num(r.max_dd_pct, 1)}</td><td>{r.vs_hold?.strategy_return_per_dd ?? "—"}</td><td>{fmt.num(r.win_rate_pct, 0)}</td>
                  <td>{fmt.num(r.profit_factor, 2)}</td><td>{fmt.num(r.final_test?.profit_factor, 2)}</td><td>{fmt.num(r.total_costs, 2)}</td>
                  <td><HoldBadge v={r.vs_hold} /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <p className="tiny muted">{data.honesty}</p>
        </div>
      )}
    </Card>
  );
}
