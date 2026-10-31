import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Modal, PasswordPrompt, fmt, useAction, useApi } from "../components.jsx";

const FORMS = {
  capital: {
    help: (
      <>
        Capital.com web platform → <b>Settings → Security</b>: turn on 2FA. Then <b>Settings → API integrations → Generate API key</b>,
        set a custom API password and copy the key (shown once). Use your Demo account first.
      </>
    ),
    fields: [
      { k: "identifier", label: "Login email", type: "email" },
      { k: "api_key", label: "API key", secret: true },
      { k: "api_password", label: "API key password", secret: true, hint: "The custom password you set for the key (not your login password)" },
      { k: "account_id", label: "Account ID (optional)", hint: "Leave empty to use the default account" },
    ],
  },
  ctrader: {
    help: (
      <>
        openapi.ctrader.com → your approved app: copy <b>Client ID</b> and <b>Client secret</b>. Open <b>Playground → Get token</b> (scope
        "trading"), tick your account and copy the access and refresh tokens.
      </>
    ),
    fields: [
      { k: "client_id", label: "Client ID" },
      { k: "client_secret", label: "Client secret", secret: true },
      { k: "access_token", label: "Access token", secret: true },
      { k: "refresh_token", label: "Refresh token", secret: true },
      { k: "account_login", label: "Account number (optional)", hint: "Needed only if the token covers several accounts" },
    ],
  },
};

const statusTone = (s) => ({ connected: "ok", idle: "muted", connecting: "warn", error: "bad" }[s] || "muted");

function ConnectorCard({ name, c, active, reload }) {
  const { can } = useAuth();
  const [run, busy] = useAction();
  const form = FORMS[name];
  const [vals, setVals] = useState({});
  const [prompt, setPrompt] = useState(null);
  useEffect(() => {
    const v = {};
    (form?.fields || []).forEach((f) => (v[f.k] = f.secret ? "" : c.fields[f.k] || ""));
    if (name === "capital") v.environment = c.fields.environment || "demo";
    setVals(v);
  }, [c, name]);
  const isActive = active === name;
  const editable = can("system.settings") && form;
  const save = (password) => {
    const fields = {};
    Object.entries(vals).forEach(([k, v]) => {
      const f = form.fields.find((x) => x.k === k);
      if (f?.secret && !v) return; // keep stored secret
      fields[k] = v;
    });
    return run(() => api.put(`/brokers/${name}`, { fields, password }), "Saved — the connector reconnects within a few seconds").then(reload);
  };
  return (
    <Card
      title={<span className="row gap">{c.name} {isActive && <Badge tone="info">ACTIVE</Badge>}</span>}
      actions={<Badge tone={statusTone(c.status)} title={c.message}>{c.status}{c.age_s !== null && c.age_s !== undefined ? ` · ${fmt.ago(c.age_s)}` : ""}</Badge>}
    >
      {c.message && c.status === "error" && <div className="alert bad small">{c.message}</div>}
      {c.account?.server && (
        <div className="kv small" style={{ marginBottom: 12 }}>
          <div><span className="muted">Account</span><span>{c.account.login} · {c.account.server}</span></div>
          <div><span className="muted">Balance / Equity</span><span>{fmt.money(c.account.balance)} / {fmt.money(c.account.equity)} {c.account.currency}</span></div>
        </div>
      )}
      {form ? (
        <>
          <p className="muted small">{form.help}</p>
          <div className="grid form">
            {form.fields.map((f) => (
              <Field key={f.k} label={f.label} hint={f.secret ? (c.fields[`${f.k}_set`] ? "Saved ✓ — leave empty to keep" : "Not set") : f.hint}>
                <input type={f.secret ? "password" : f.type || "text"} autoComplete="off" disabled={!editable} value={vals[f.k] ?? ""}
                  placeholder={f.secret && c.fields[`${f.k}_set`] ? "••••••••" : ""} onChange={(e) => setVals({ ...vals, [f.k]: e.target.value })} />
              </Field>
            ))}
            {name === "capital" && (
              <Field label="Environment" hint="Live requires the approved live gate">
                <select disabled={!editable} value={vals.environment || "demo"} onChange={(e) => setVals({ ...vals, environment: e.target.value })}>
                  <option value="demo">Demo (practice money)</option>
                  <option value="live">LIVE (real money)</option>
                </select>
              </Field>
            )}
          </div>
        </>
      ) : (
        <p className="muted small">Runs on your Windows machine next to MetaTrader 5 (see <code>bridge/README.md</code>). Activate it here once the bridge is installed.</p>
      )}
      <div className="row end gap">
        {editable && <button className="btn" disabled={busy} onClick={() => setPrompt("save")}>Save credentials</button>}
        {can("trading.control") && !isActive && <button className="btn primary" disabled={busy || !c.configured} onClick={() => setPrompt("activate")} title={c.configured ? "" : "Save credentials first"}>Use this broker</button>}
        {can("trading.control") && isActive && <button className="btn ghost" disabled={busy} onClick={() => setPrompt("deactivate")}>Disconnect</button>}
      </div>
      {prompt && (
        <PasswordPrompt
          title={prompt === "save" ? `Save ${c.name} credentials` : prompt === "activate" ? `Use ${c.name}` : `Disconnect ${c.name}`}
          message={prompt === "activate" ? "The app will take prices and execute broker orders through this connection. Other brokers are disconnected." : prompt === "deactivate" ? "No broker will feed the app until you choose one again." : "Credentials are stored encrypted on your server."}
          onClose={() => setPrompt(null)}
          onSubmit={(password) =>
            prompt === "save" ? save(password)
              : run(() => api.post("/brokers/activate", { name: prompt === "activate" ? name : "none", password }), prompt === "activate" ? `${c.name} is now the active broker` : "Disconnected").then(reload)}
        />
      )}
    </Card>
  );
}

function Limits({ data, reload }) {
  const { can } = useAuth();
  const [run] = useAction();
  const [v, setV] = useState(null);
  const [prompt, setPrompt] = useState(false);
  useEffect(() => setV({ max_volume: data.max_volume, max_spread_points: data.max_spread_points, allow_real: data.allow_real }), [data]);
  if (!v) return null;
  return (
    <Card title="Connector safety limits" actions={<Badge tone={data.allow_real ? "bad" : "ok"}>{data.allow_real ? "Real-money orders ALLOWED" : "Real-money orders blocked"}</Badge>}>
      <p className="muted small">Enforced by the connector itself, in addition to the risk engine.</p>
      <div className="grid form">
        <Field label="Max lots per order"><input type="number" step="0.01" disabled={!can("risk.edit")} value={v.max_volume} onChange={(e) => setV({ ...v, max_volume: e.target.value })} /></Field>
        <Field label="Max spread (points)"><input type="number" disabled={!can("risk.edit")} value={v.max_spread_points} onChange={(e) => setV({ ...v, max_spread_points: e.target.value })} /></Field>
        <Field label="Allow real-money orders" hint="Also requires the approved live gate and BROKER LIVE mode">
          <select disabled={!can("live.approve")} value={String(v.allow_real)} onChange={(e) => setV({ ...v, allow_real: e.target.value === "true" })}>
            <option value="false">No (demo only)</option><option value="true">Yes</option>
          </select>
        </Field>
      </div>
      {can("risk.edit") && <div className="row end"><button className="btn" onClick={() => setPrompt(true)}>Save limits</button></div>}
      {prompt && <PasswordPrompt title="Save connector limits" onClose={() => setPrompt(false)}
        onSubmit={(password) => run(() => api.put("/brokers", { max_volume: Number(v.max_volume), max_spread_points: Number(v.max_spread_points), allow_real: v.allow_real, password }), "Limits saved").then(reload)} />}
    </Card>
  );
}

const CRYPTO_MODES = [
  { k: "OFF", label: "Off", d: "Prices and charts only" },
  { k: "PAPER", label: "Practice", d: "Virtual USDT at real exchange prices — no keys needed" },
  { k: "TEST", label: "Testnet / demo", d: "Real orders on the exchange's test environment" },
  { k: "LIVE", label: "LIVE", d: "Real money — needs password and 'allow real'" },
];
const HOW_KEYS = {
  binance: "Binance → Profile → API Management → Create API (system generated). Enable 'Spot & Margin Trading' only. Do NOT enable withdrawals. Restrict access to your server IP 37.60.252.21. For testnet keys use testnet.binance.vision.",
  okx: "OKX → Profile → API → Create V5 API key. Permission: Trade only (NOT Withdraw). Bind IP 37.60.252.21. Write down the passphrase. For demo keys switch to Demo trading first, then create the key there.",
  bybit: "Bybit → Profile → API → Create new key (system generated). Permissions: Spot trade only, no withdrawals. Restrict IP 37.60.252.21. For testnet keys use testnet.bybit.com.",
};

function CryptoCard() {
  const { data: c, reload } = useApi("/crypto/settings", { interval: 15000 });
  const [run, busy] = useAction();
  const [keys, setKeys] = useState(null);
  const [pw, setPw] = useState(null);
  const [syms, setSyms] = useState(null);
  const [test, setTest] = useState(null);
  if (!c) return <Card title="Crypto exchanges"><p className="muted">Loading…</p></Card>;
  const exch = c.exchanges.find((x) => x.name === c.exchange);
  const setMode = (m) => {
    if (m === "LIVE") setPw({ mode: "LIVE", allow_real: true });
    else run(() => api.put("/crypto/settings", { mode: m }), `Crypto mode: ${m}`).then(reload).catch(() => {});
  };
  return (
    <Card title="Crypto exchanges (spot)" actions={<Badge tone={c.status.market_ok ? "ok" : "warn"}>{c.status.market_ok ? "prices live" : "prices offline"}</Badge>}>
      <p className="small muted">Buy and sell coins with USDT on Binance, OKX or Bybit. Spot only — no futures, no borrowing. Every buy gets a stop-loss order on the exchange immediately; if that fails the coins are sold again.</p>
      <Field label="Exchange">
        <div className="seg">{c.exchanges.map((x) => <button key={x.name} type="button" className={c.exchange === x.name ? "active" : ""}
          onClick={() => run(() => api.put("/crypto/settings", { exchange: x.name }), `Exchange: ${x.label}`).then(reload).catch(() => {})}>{x.label}{x.has_keys ? " 🔑" : ""}</button>)}</div>
      </Field>
      <Field label="Mode">
        <div className="mode-grid">{CRYPTO_MODES.map((m) => (
          <button key={m.k} className={`mode ${c.mode === m.k ? "on" : ""} ${m.k === "LIVE" ? "danger" : ""}`} onClick={() => setMode(m.k)}>
            <strong>{m.label}{m.k === "TEST" ? ` (${exch.test_label})` : ""}</strong><span>{m.d}</span></button>))}</div>
      </Field>
      <div className="row gap wrap small">
        <span>API key: {exch.has_keys ? <b>{exch.api_key}</b> : <span className="muted">not saved</span>}</span>
        <button className="btn sm" onClick={() => setKeys({ api_key: "", secret: "", passphrase: "", password: "" })}>{exch.has_keys ? "Replace keys" : "Add API keys"}</button>
        {exch.has_keys && <button className="btn sm ghost" disabled={busy} onClick={() => run(() => api.post("/crypto/test-connection", { exchange: c.exchange, environment: c.mode === "LIVE" ? "LIVE" : "TEST" })).then(setTest).catch(() => {})}>Test connection</button>}
      </div>
      {test && <div className={`alert ${test.ok ? "ok" : "bad"} small`}>{test.ok ? `Connected. Balances: ${Object.entries(test.assets || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "empty"}` : test.error}
        {test.warnings?.map((w) => <div key={w} className="neg"><b>⚠ {w}</b></div>)}</div>}
      <Field label="Coins to watch / trade (USDT pairs)">
        <div className="row gap"><input value={syms ?? c.symbols.join(", ")} onChange={(e) => setSyms(e.target.value.toUpperCase())} />
          <button className="btn sm" disabled={syms === null} onClick={() => run(() => api.put("/crypto/settings", { symbols: syms.split(",").map((x) => x.trim()).filter(Boolean) }), "Saved").then(() => { setSyms(null); reload(); }).catch(() => {})}>Save</button></div>
      </Field>
      <Field label="Crypto strategy" hint="Compare them on the Strategy lab page first">
        <select value={c.strategy || ""} onChange={(e) => run(() => api.put("/crypto/settings", { strategy: e.target.value }), "Crypto strategy saved").then(reload).catch(() => {})}>
          <option value="">Same as forex</option>
          {Object.entries(CRYPTO_STRATS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
      <div className="row gap wrap small">
        <label className="check"><input type="checkbox" checked={!!c.signals} onChange={(e) => run(() => api.put("/crypto/settings", { signals: e.target.checked }), "Saved").then(reload)} /> Strategy looks for setups on crypto too (you approve each one, unless auto-trade is on)</label>
        <span className="muted">Practice cash: {fmt.money(c.paper_cash)} USDT</span>
        <button className="btn sm ghost" onClick={() => { const v = window.prompt("Reset practice account to how many USDT?", "100"); if (v) run(() => api.put("/crypto/settings", { paper_reset: Number(v) }), "Practice account reset").then(reload).catch(() => {}); }}>Reset practice</button>
      </div>
      {c.status.market_error && <p className="small neg">Market data: {c.status.market_error}</p>}
      {keys && (
        <Modal title={`${exch.label} API keys`} onClose={() => setKeys(null)}>
          <div className="alert warn small"><b>Safety:</b> {HOW_KEYS[c.exchange]}</div>
          <Field label="API key"><input value={keys.api_key} onChange={(e) => setKeys({ ...keys, api_key: e.target.value })} autoComplete="off" /></Field>
          <Field label="Secret key"><input type="password" value={keys.secret} onChange={(e) => setKeys({ ...keys, secret: e.target.value })} autoComplete="off" /></Field>
          {exch.needs_passphrase && <Field label="API passphrase"><input type="password" value={keys.passphrase} onChange={(e) => setKeys({ ...keys, passphrase: e.target.value })} autoComplete="off" /></Field>}
          <Field label="Your app password (to confirm)"><input type="password" value={keys.password} onChange={(e) => setKeys({ ...keys, password: e.target.value })} autoComplete="current-password" /></Field>
          <div className="row end">
            <button className="btn ghost" onClick={() => setKeys(null)}>Cancel</button>
            <button className="btn primary" disabled={busy} onClick={() => run(() => api.put(`/crypto/keys/${c.exchange}`, keys), "Keys saved (encrypted)").then(() => { setKeys(null); reload(); }).catch(() => {})}>Save keys</button>
          </div>
          <p className="tiny muted">Keys are encrypted on your server and never shown again. Testnet/demo keys and live keys are different — save the ones that match the mode you use.</p>
        </Modal>
      )}
      {pw && <PasswordPrompt title="Enable LIVE crypto trading" message="Real money on the exchange. Orders still need your approval or manual ticket, and every buy gets a stop-loss at the exchange."
        onClose={() => setPw(null)} onSubmit={(password) => run(() => api.put("/crypto/settings", { ...pw, password }), "Crypto LIVE enabled").then(() => { setPw(null); reload(); })} />}
    </Card>
  );
}

const CRYPTO_STRATS = {
  donchian_breakout: "Donchian breakout (trend, win ~30–45%, big winners)",
  rsi2_meanrev: "RSI-2 pullback (mean reversion, win ~55–70%, small winners)",
  bb_squeeze: "Bollinger squeeze breakout",
  ema_cross_adx: "EMA 9/21 cross + ADX (momentum)",
  trend_pullback: "EMA trend pullback (baseline)",
  smc_sweep_bos: "SMC sweep → BOS → retest",
};

export default function Brokers() {
  const { data, reload } = useApi("/brokers", { interval: 5000 });
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <div className="alert ok small">
        Manage your broker connections here; no server commands are needed. Credentials are encrypted on your server and never shown again.
        After saving, the connector reconnects automatically within a few seconds. Only one broker is active at a time.
      </div>
      <div className="grid two">
        {["capital", "ctrader", "mt5"].map((n) => <ConnectorCard key={n} name={n} c={data.connectors[n]} active={data.active} reload={reload} />)}
        <Limits data={data} reload={reload} />
      </div>
      <CryptoCard />
    </div>
  );
}
