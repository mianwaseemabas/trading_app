import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Modal, fmt, pnlClass, useAction, useApi } from "../components.jsx";

const MODE_NAME = { PAPER: "Practice (paper)", MT5_DEMO: "Broker demo", MT5_LIVE: "Broker LIVE", CRYPTO_PAPER: "Crypto practice", CRYPTO_TEST: "Crypto testnet", CRYPTO_LIVE: "Crypto LIVE" };

function Autopilot({ on, reload }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const { data: plan, reload: reloadPlan } = useApi(open ? "/autopilot" : null, { deps: [open] });
  const [run, busy] = useAction();
  if (!can("trading.control")) return null;
  return (
    <>
      {on
        ? <button className="btn" disabled={busy} onClick={() => { if (window.confirm("Stop autopilot? Auto-trade turns off everywhere; new setups will wait for your approval. Open trades keep their stops.")) run(() => api.post("/autopilot/stop"), "Autopilot stopped").then(reload); }}>■ Stop autopilot</button>
        : <button className="btn primary" onClick={() => setOpen(true)}>▶ Start autopilot (practice)</button>}
      <a className="btn" href="#/auto">⚙ Auto-trade settings</a>
      {open && (
        <Modal title="Start autopilot — practice money only" onClose={() => setOpen(false)}>
          {!plan ? <p className="muted">Preparing…</p> : plan.blocked ? <div className="alert bad">{plan.blocked}</div> : <>
            <p className="small">The app will trade qualified setups by itself on <b>{plan.accounts.map((m) => MODE_NAME[m] || m).join(", ")}</b> and tell you about every trade.
              Real money stays OFF. These settings will be applied:</p>
            <ol className="ap-steps">{plan.plan.map((s) => <li key={s.what}><b>{s.what}</b><div className="tiny muted">{s.detail}</div></li>)}</ol>
            <p className="tiny muted">You can stop it any time on this screen (or with /kill in Telegram). The risk engine checks every order.</p>
            <div className="row end gap">
              <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn primary" disabled={busy} onClick={() => run(() => api.post("/autopilot/start"), "Autopilot started 🤖").then(() => { setOpen(false); reload(); reloadPlan(); }).catch(() => {})}>Start autopilot</button>
            </div>
          </>}
        </Modal>
      )}
    </>
  );
}

export default function Home() {
  const { user } = useAuth();
  const { data: h, reload } = useApi("/home", { interval: 20000 });
  if (!h) return <p className="muted">Loading…</p>;
  const t = h.today;
  const hour = new Date().getHours();
  const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return (
    <div className="stack home">
      <div className={`home-hero ${h.needs.length ? "attn" : "calm"}`}>
        <div>
          <div className="tiny muted">{hello}, {user.full_name || user.username}</div>
          <h2>{h.needs.length ? `${h.needs.length} thing${h.needs.length > 1 ? "s" : ""} need${h.needs.length > 1 ? "" : "s"} you` : "Nothing to do — the app is working ✓"}</h2>
          <div className="small">
            {h.autopilot.on ? <>🤖 <b>Autopilot ON</b> since {fmt.dt(h.autopilot.since)} on {h.autopilot.accounts.map((m) => MODE_NAME[m] || m).join(", ")}. Real money: OFF.</>
              : <>Autopilot is off — qualified setups wait for your approval{h.autopilot.accounts.length ? ` (auto-trade still on: ${h.autopilot.accounts.map((m) => MODE_NAME[m] || m).join(", ")})` : ""}.</>}
          </div>
        </div>
        <Autopilot on={h.autopilot.on} reload={reload} />
      </div>

      {h.needs.length > 0 && (
        <Card title="Needs you">
          <ul className="needs">{h.needs.map((n, i) => (
            <li key={i} className={n.kind}><span>{n.kind === "approve" ? "✋" : "⚠"}</span><span>{n.text}</span><a className="btn sm" href={n.link}>{n.kind === "approve" ? "Decide" : "Fix"} →</a></li>
          ))}</ul>
        </Card>
      )}

      <div className="home-grid">
        <Card title="Is everything OK?" actions={<Badge tone={h.all_ok ? "good" : "bad"}>{h.all_ok ? "all good" : "check"}</Badge>}>
          <ul className="lights">{h.lights.map((l) => (
            <li key={l.label}><span className={`dot ${l.state}`} /><b>{l.label}</b><span className="tiny muted">{l.detail}</span></li>
          ))}</ul>
        </Card>

        <Card title="Today" actions={<a className="small" href="#/opps">Opportunities →</a>}>
          <div className="today">
            <div><span>Trades opened</span><b>{t.opened}</b></div>
            <div><span>Closed</span><b>{t.closed}</b></div>
            <div><span>Result (closed)</span><b className={pnlClass(t.realized)}>{t.realized > 0 ? "+" : ""}{fmt.money(t.realized)}</b></div>
            <div><span>Open now</span><b>{t.open} <small className={pnlClass(t.unrealized)}>({t.unrealized > 0 ? "+" : ""}{fmt.money(t.unrealized)})</small></b></div>
          </div>
          {t.positions.length > 0 && <table className="tbl"><tbody>{t.positions.map((p) => (
            <tr key={p.id}><td><b>{p.symbol}</b> <span className={p.side === "BUY" ? "pos" : "neg"}>{p.side}</span><div className="tiny muted">{MODE_NAME[p.mode] || p.mode}</div></td>
              <td className="tiny">SL {p.sl ?? <b className="neg">none!</b>} · TP {p.tp ?? "—"}</td><td className={`num ${pnlClass(p.pnl)}`}>{fmt.money(p.pnl)}</td></tr>))}</tbody></table>}
          {t.positions.length === 0 && <p className="small muted">No open trades.</p>}
        </Card>

        <Card title="Today's big news">
          {h.news.length === 0 ? <p className="small muted">No high-impact events in the next 12 hours.</p>
            : <ul className="small news-mini">{h.news.map((n, i) => <li key={i}><b>{new Date(n.time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</b> {n.currency} — {n.title}</li>)}</ul>}
          <p className="tiny muted">New trades on these currencies pause around the release (news filter).</p>
        </Card>

        <Card title="This week · road to real money">
          <div className="today">
            <div><span>Closed trades (7 days)</span><b>{h.week.trades}</b></div>
            <div><span>Net result</span><b className={pnlClass(h.week.net)}>{h.week.net > 0 ? "+" : ""}{fmt.money(h.week.net)}</b></div>
            <div><span>Win rate</span><b>{h.week.win_rate != null ? `${h.week.win_rate}%` : "—"}</b></div>
          </div>
          <div className="small" style={{ marginTop: 8 }}>Live-gate evidence: <b>{h.readiness.passed}/{h.readiness.total}</b> ready</div>
          <div className="meter-t"><div className="meter-f ok" style={{ width: `${(100 * h.readiness.passed) / Math.max(1, h.readiness.total)}%` }} /></div>
          {h.readiness.missing.length > 0 && <ul className="tiny muted">{h.readiness.missing.map((m) => <li key={m}>{m}</li>)}</ul>}
        </Card>
      </div>
      <p className="tiny muted">Your routine: ☀️ read the morning brief (Telegram) · 📱 when your phone buzzes, approve or ignore · 📅 Sunday: read the weekly coach. That's all.</p>
    </div>
  );
}
