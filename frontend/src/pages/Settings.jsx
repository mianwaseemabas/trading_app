import { BackupCard, SecurityCard } from "../components/Helpers.jsx";
import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, PasswordPrompt, useAction, useApi } from "../components.jsx";

export default function Settings() {
  const { can } = useAuth();
  const { data: status, reload } = useApi("/system/status");
  const { data: notif, reload: reloadN } = useApi("/system/notifications");
  const [run, busy] = useAction();
  const [n, setN] = useState(null);
  const [paper, setPaper] = useState(false);
  const [paperBal, setPaperBal] = useState(500);
  useEffect(() => { if (notif) setN(notif); }, [notif]);
  if (!status) return <p className="muted">Loading…</p>;
  const src = status.data.source;
  const switchSource = (to) => {
    const msg = to === "simulator"
      ? "Switch to SIMULATED data? Stored broker candles will be deleted so synthetic and real data never mix."
      : "Switch to broker data (cTrader / MT5)? All simulated candles and quotes will be deleted.";
    if (window.confirm(msg)) run(() => api.put("/system/data", { source: to }), `Data source: ${to}`).then(reload).catch(() => {});
  };
  return (
    <div className="stack">
      <Card title="Market data source" actions={<Badge tone={src === "mt5" ? "ok" : "warn"}>{src}</Badge>}>
        <p><b>Broker</b>: real quotes, candles, account data and execution from your broker — via the cTrader connector (<code>deploy/setup-ctrader.sh</code>) or the Windows MT5 bridge (<code>bridge/README.md</code>).<br /><b>Simulator</b>: synthetic random-walk prices to test the dashboard, signals and paper execution without a broker. Never use simulator results to judge the strategy.</p>
        <div className="row gap">
          <button className="btn" disabled={busy || src === "mt5"} onClick={() => switchSource("mt5")}>Use broker data</button>
          <button className="btn" disabled={busy || src === "simulator"} onClick={() => switchSource("simulator")}>Use simulator</button>
        </div>
        <p className="muted small">Trading mode must be OFF or SIGNALS ONLY and no positions may be open to switch.</p>
      </Card>
      <Card title="Broker connection">
        <div className="kv">
          <div><span className="muted">Configured (BRIDGE_SECRET set)</span><span>{status.bridge.configured ? "yes" : "no"}</span></div>
          <div><span className="muted">Last seen</span><span>{status.bridge.last_seen || "never"}</span></div>
          <div><span className="muted">From IP</span><span>{status.bridge.ip || "—"}</span></div>
          <div><span className="muted">Bridge version</span><span>{status.bridge.version || "—"}</span></div>
          <div><span className="muted">Account</span><span>{status.account_meta?.login ? `${status.account_meta.login} @ ${status.account_meta.server} (${status.account_meta.trade_mode})` : "—"}</span></div>
          <div><span className="muted">Reconciliation</span><span>{status.reconciliation.ok ? "OK" : status.reconciliation.detail}</span></div>
        </div>
      </Card>
      <Card title="Notifications">
        <p className="small">Telegram, email and phone notifications — and the Telegram bot — are set up on the <a href="#/alerts">Alerts</a> page.</p>
      </Card>
      <SecurityCard />
      <BackupCard />
      {can("trading.control") && (
        <Card title="Paper account">
          <p className="muted small">The paper account simulates execution at live bid/ask with commission. Reset requires no open paper positions.</p>
          <div className="row gap end">
            <Field label="New balance (USD)"><input type="number" value={paperBal} onChange={(e) => setPaperBal(e.target.value)} /></Field>
            <button className="btn" onClick={() => setPaper(true)}>Reset paper account</button>
          </div>
          {paper && <PasswordPrompt title="Reset paper account" onClose={() => setPaper(false)} onSubmit={(password) => run(() => api.post("/system/paper/reset", { balance: Number(paperBal), password }), "Paper account reset").then(reload)} />}
        </Card>
      )}
    </div>
  );
}
