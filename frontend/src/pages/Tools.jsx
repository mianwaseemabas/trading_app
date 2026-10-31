import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { Card, Field, fmt, useAction, useApi, useWidth } from "../components.jsx";

function Calculator() {
  const { data: wl } = useApi("/intel/watchlist");
  const [f, setF] = useState({ symbol: "EURUSD", entry: "", stop: "", target: "", risk: "5" });
  const [res, setRes] = useState(null);
  const [run, busy] = useAction();
  const w = (wl || []).find((x) => x.symbol === f.symbol);
  const calc = () => run(() => api.post("/tools/size", { symbol: f.symbol, entry: Number(f.entry || w?.ask), stop: Number(f.stop), target: f.target ? Number(f.target) : null, risk_amount: Number(f.risk) })).then(setRes).catch(() => {});
  return (
    <Card title="📐 Position size calculator">
      <p className="small muted">How big should my trade be so that hitting the stop-loss loses exactly the amount I choose?</p>
      <div className="grid form">
        <Field label="Market"><select value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value, entry: "", stop: "", target: "" })}>{(wl || []).map((x) => <option key={x.symbol}>{x.symbol}</option>)}</select></Field>
        <Field label="Entry price" hint={w ? `now ${w.ask}` : ""}><input inputMode="decimal" value={f.entry} placeholder={w?.ask ?? ""} onChange={(e) => setF({ ...f, entry: e.target.value })} /></Field>
        <Field label="Stop-loss price"><input inputMode="decimal" value={f.stop} onChange={(e) => setF({ ...f, stop: e.target.value })} /></Field>
        <Field label="Take-profit (optional)"><input inputMode="decimal" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} /></Field>
        <Field label="Money I accept to lose"><input inputMode="decimal" value={f.risk} onChange={(e) => setF({ ...f, risk: e.target.value })} /></Field>
      </div>
      <button className="btn primary" disabled={busy || !f.stop || !f.risk} onClick={calc}>Calculate</button>
      {res && (
        <div className="calc-out">
          <div><span>Trade size</span><b>{res.size} {res.units_label}</b></div>
          <div><span>Loss if stop hits</span><b className="neg">−{fmt.money(res.risk_at_size)}</b></div>
          {res.reward != null && <div><span>Profit if target hits</span><b className="pos">+{fmt.money(res.reward)}</b></div>}
          <div><span>Stop distance</span><b>{res.stop_pips != null ? `${res.stop_pips} pips` : ""} ({res.stop_pct}%)</b></div>
          {res.rr != null && <div><span>Reward : risk</span><b>{res.rr} : 1</b></div>}
          {res.break_even_win_rate_pct != null && <div><span>Must win at least</span><b>{res.break_even_win_rate_pct}% of trades to break even</b></div>}
          <div><span>Position value</span><b>{fmt.money(res.notional)}</b></div>
          {res.warning && <div className="alert warn small">{res.warning}</div>}
        </div>
      )}
    </Card>
  );
}

function simulate({ balance, riskPct, winRate, avgWin, trades, runs, compound }) {
  const paths = [];
  const finals = [], dds = [], streaks = [];
  for (let r = 0; r < runs; r++) {
    let eq = balance, peak = balance, dd = 0, streak = 0, worst = 0;
    const path = [eq];
    for (let t = 0; t < trades; t++) {
      const risk = (compound ? eq : balance) * riskPct / 100;
      if (Math.random() < winRate / 100) { eq += risk * avgWin; streak = 0; } else { eq -= risk; streak += 1; worst = Math.max(worst, streak); }
      peak = Math.max(peak, eq);
      dd = Math.max(dd, (peak - eq) / peak);
      path.push(eq);
    }
    paths.push(path); finals.push(eq); dds.push(dd); streaks.push(worst);
  }
  const pct = (arr, p) => { const s = [...arr].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
  const bands = [0.05, 0.5, 0.95].map((p) => Array.from({ length: trades + 1 }, (_, t) => pct(paths.map((x) => x[t]), p)));
  const exp = (winRate / 100) * avgWin - (1 - winRate / 100);
  return { bands, median: pct(finals, 0.5), p5: pct(finals, 0.05), p95: pct(finals, 0.95), lossProb: finals.filter((x) => x < balance).length / runs,
    dd20: dds.filter((x) => x >= 0.2).length / runs, medDD: pct(dds, 0.5), medStreak: pct(streaks, 0.5), worstStreak: pct(streaks, 0.95), exp, sample: paths.slice(0, 12) };
}

function Bands({ sim, balance }) {
  const [ref, w] = useWidth();
  const H = 240, pad = { l: 8, r: 64, t: 10, b: 20 };
  const all = sim.bands.flat().concat(sim.sample.flat());
  const lo = Math.min(...all, balance), hi = Math.max(...all, balance);
  const n = sim.bands[0].length;
  const X = (i) => pad.l + (i / (n - 1)) * (w - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
  const path = (arr) => arr.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const area = `${path(sim.bands[2])} ${sim.bands[0].map((v, i) => `L${X(n - 1 - i).toFixed(1)},${Y(sim.bands[0][n - 1 - i]).toFixed(1)}`).join("")} Z`;
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={H}>
        <path d={area} fill="rgba(47,123,255,.15)" />
        {sim.sample.map((p, k) => <path key={k} d={path(p)} fill="none" stroke="rgba(148,163,184,.25)" strokeWidth="1" />)}
        <line x1={pad.l} x2={w - pad.r} y1={Y(balance)} y2={Y(balance)} stroke="var(--muted)" strokeDasharray="4 4" />
        <path d={path(sim.bands[1])} fill="none" stroke="var(--accent)" strokeWidth="2" />
        {[sim.bands[0][n - 1], sim.bands[1][n - 1], sim.bands[2][n - 1]].map((v, k) => <text key={k} x={w - pad.r + 4} y={Y(v) + 4} className="axis">{fmt.money(v)}</text>)}
        <text x={pad.l} y={H - 4} className="axis">trade 0</text><text x={w - pad.r - 40} y={H - 4} className="axis">trade {n - 1}</text>
      </svg>
      <div className="tiny muted">Blue line = typical (median) result · shaded = 90% of outcomes · grey = a few individual runs · dashed = starting balance</div>
    </div>
  );
}

function Simulator() {
  const [f, setF] = useState({ balance: 500, riskPct: 1, winRate: 40, avgWin: 2, trades: 50, runs: 2000, compound: true });
  const [seed, setSeed] = useState(0);
  const sim = useMemo(() => simulate(f), [f, seed]); // eslint-disable-line
  const num = (k) => (e) => setF({ ...f, [k]: Math.max(0, Number(e.target.value) || 0) });
  return (
    <Card title="🎲 What-if simulator" actions={<button className="btn sm ghost" onClick={() => setSeed(seed + 1)}>Run again</button>}>
      <p className="small muted">“If I risk 1% for 50 trades with a 40% win rate, what happens?” — 2,000 random runs of the same rules show the range of outcomes, including bad luck streaks.</p>
      <div className="grid form">
        <Field label="Start balance"><input inputMode="decimal" value={f.balance} onChange={num("balance")} /></Field>
        <Field label="Risk per trade %"><input inputMode="decimal" value={f.riskPct} onChange={num("riskPct")} /></Field>
        <Field label="Win rate %"><input inputMode="decimal" value={f.winRate} onChange={num("winRate")} /></Field>
        <Field label="Average win (in R)" hint="2 = you win twice what you risk"><input inputMode="decimal" value={f.avgWin} onChange={num("avgWin")} /></Field>
        <Field label="Number of trades"><input inputMode="numeric" value={f.trades} onChange={(e) => setF({ ...f, trades: Math.min(1000, Math.max(5, Number(e.target.value) || 5)) })} /></Field>
        <label className="check small" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.compound} onChange={(e) => setF({ ...f, compound: e.target.checked })} /> risk % of current balance</label>
      </div>
      <Bands sim={sim} balance={f.balance} />
      <div className="calc-out">
        <div><span>Expectancy per trade</span><b className={sim.exp > 0 ? "pos" : "neg"}>{sim.exp >= 0 ? "+" : ""}{sim.exp.toFixed(2)} R</b></div>
        <div><span>Typical final balance</span><b>{fmt.money(sim.median)}</b></div>
        <div><span>Bad case (worst 5%)</span><b className="neg">{fmt.money(sim.p5)}</b></div>
        <div><span>Good case (best 5%)</span><b className="pos">{fmt.money(sim.p95)}</b></div>
        <div><span>Chance to end below start</span><b>{Math.round(sim.lossProb * 100)}%</b></div>
        <div><span>Chance of a 20%+ drawdown</span><b>{Math.round(sim.dd20 * 100)}%</b></div>
        <div><span>Losing streak to expect</span><b>{sim.medStreak} in a row (can be {sim.worstStreak})</b></div>
      </div>
      <p className="tiny muted">{sim.exp <= 0 ? "With these numbers the system loses money on average — no risk size fixes a negative expectancy." :
        `Positive expectancy, but notice the losing streaks: with ${f.riskPct}% risk, ${sim.worstStreak} losses in a row cost about ${(sim.worstStreak * f.riskPct).toFixed(1)}% of the account.`} Costs (spread/fees) lower the real win size.</p>
    </Card>
  );
}

export default function Tools() {
  return <div className="grid two"><Calculator /><Simulator /></div>;
}
