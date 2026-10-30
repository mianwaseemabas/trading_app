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
      : "Switch to MT5 broker data? All simulated candles and quotes will be deleted.";
    if (window.confirm(msg)) run(() => api.put("/system/data", { source: to }), `Data source: ${to}`).then(reload).catch(() => {});
  };
  return (
    <div className="stack">
      <Card title="Market data source" actions={<Badge tone={src === "mt5" ? "ok" : "warn"}>{src}</Badge>}>
        <p><b>MT5</b>: real quotes, candles, account data and execution through the Windows bridge (see <code>bridge/README.md</code>).<br /><b>Simulator</b>: synthetic random-walk prices to test the dashboard, signals and paper execution without a broker. Never use simulator results to judge the strategy.</p>
        <div className="row gap">
          <button className="btn" disabled={busy || src === "mt5"} onClick={() => switchSource("mt5")}>Use MT5 data</button>
          <button className="btn" disabled={busy || src === "simulator"} onClick={() => switchSource("simulator")}>Use simulator</button>
        </div>
        <p className="muted small">Trading mode must be OFF or SIGNALS ONLY and no positions may be open to switch.</p>
      </Card>
      <Card title="MT5 bridge">
        <div className="kv">
          <div><span className="muted">Configured (BRIDGE_SECRET set)</span><span>{status.bridge.configured ? "yes" : "no"}</span></div>
          <div><span className="muted">Last seen</span><span>{status.bridge.last_seen || "never"}</span></div>
          <div><span className="muted">From IP</span><span>{status.bridge.ip || "—"}</span></div>
          <div><span className="muted">Bridge version</span><span>{status.bridge.version || "—"}</span></div>
          <div><span className="muted">Account</span><span>{status.account_meta?.login ? `${status.account_meta.login} @ ${status.account_meta.server} (${status.account_meta.trade_mode})` : "—"}</span></div>
          <div><span className="muted">Reconciliation</span><span>{status.reconciliation.ok ? "OK" : status.reconciliation.detail}</span></div>
        </div>
      </Card>
      {n && (
        <Card title="Notifications (Telegram)" actions={<Badge tone={n.telegram_configured ? "ok" : "muted"}>{n.telegram_configured ? "bot configured" : "not configured"}</Badge>}>
          {!n.telegram_configured && <p className="muted small">Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the server .env file and restart to enable.</p>}
          <div className="row gap wrap">
            <label className="check"><input type="checkbox" checked={n.telegram_enabled} onChange={(e) => setN({ ...n, telegram_enabled: e.target.checked })} /> Enabled</label>
            <label className="check"><input type="checkbox" checked={n.notify_orders} onChange={(e) => setN({ ...n, notify_orders: e.target.checked })} /> Orders & fills</label>
            <label className="check"><input type="checkbox" checked={n.notify_signals} onChange={(e) => setN({ ...n, notify_signals: e.target.checked })} /> Untraded signals</label>
          </div>
          <p className="muted small">Kill switch, loss-limit locks and reconciliation problems are always sent when the bot is configured.</p>
          <div className="row end gap">
            <button className="btn ghost" disabled={!n.telegram_configured} onClick={() => run(() => api.post("/system/notifications/test"), "Test sent").catch(() => {})}>Send test</button>
            <button className="btn primary" onClick={() => run(() => api.put("/system/notifications", { telegram_enabled: n.telegram_enabled, notify_orders: n.notify_orders, notify_signals: n.notify_signals }), "Saved").then(reloadN)}>Save</button>
          </div>
        </Card>
      )}
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
