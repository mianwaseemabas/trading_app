import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Modal, PasswordPrompt, Switch, fmt, useAction, useApi } from "../components.jsx";

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function AiHelpersCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/ai-jobs");
  const [f, setF] = useState(null);
  const [run, busy] = useAction();
  const [out, setOut] = useState(null);
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return null;
  const edit = can("system.settings");
  const save = (patch) => run(() => api.put("/ai-jobs", patch), "Saved").then(reload).catch(() => {});
  return (
    <Card title="☀️ AI morning brief & 🧭 weekly coach" actions={!data.ai_ready && <Badge tone="warn">AI off — plain summaries</Badge>}>
      <p className="small muted">Sent as alerts to Telegram / phone / email (choose channels on the Alerts page: AI BRIEF, AI COACH).</p>
      <div className="grid two">
        <div className="helper-box">
          <Switch on={f.brief_enabled} disabled={!edit || busy} label="Morning brief every day" onChange={(v) => save({ brief_enabled: v })} />
          <div className="tiny muted">Today's high-impact news for your markets, the trend on each market, your open trades and setups waiting.</div>
          <div className="row gap wrap" style={{ marginTop: 8 }}>
            <Field label="Time"><input type="time" disabled={!edit} value={f.brief_time} onChange={(e) => setF({ ...f, brief_time: e.target.value })} onBlur={() => save({ brief_time: f.brief_time })} /></Field>
            <Field label="Time zone"><input disabled={!edit} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} onBlur={() => save({ timezone: f.timezone })} /></Field>
          </div>
          {can("ai.use") && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post("/ai-jobs/brief")).then((r) => setOut({ t: "Morning brief", text: r.text })).catch(() => {})}>Send a brief now</button>}
        </div>
        <div className="helper-box">
          <Switch on={f.coach_enabled} disabled={!edit || busy} label="Weekly coach" onChange={(v) => save({ coach_enabled: v })} />
          <div className="tiny muted">Claude reviews your week's closed trades and journal notes and sends three lessons.</div>
          <div className="row gap wrap" style={{ marginTop: 8 }}>
            <Field label="Day"><select disabled={!edit} value={f.coach_day} onChange={(e) => save({ coach_day: Number(e.target.value) })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></Field>
            <Field label="Time"><input type="time" disabled={!edit} value={f.coach_time} onChange={(e) => setF({ ...f, coach_time: e.target.value })} onBlur={() => save({ coach_time: f.coach_time })} /></Field>
          </div>
          {can("ai.use") && <button className="btn sm" disabled={busy} onClick={() => run(() => api.post("/ai-jobs/coach")).then((r) => setOut({ t: "Weekly coach", text: r.text })).catch(() => {})}>Review my week now</button>}
        </div>
      </div>
      {out && <Modal title={out.t} onClose={() => setOut(null)} wide><div className="brief">{out.text}</div><p className="tiny muted">Also sent to your alert channels.</p></Modal>}
    </Card>
  );
}

const CMDS = [["/status", "mode, equity, positions, auto-trade"], ["/positions", "open trades with P/L"], ["/setups", "setups waiting for you"],
  ["/price BTCUSDT", "latest price"], ["/brief", "AI brief now"], ["/ask …", "ask the AI coach"], ["/approve 123 PIN", "approve setup #123"],
  ["/decline 123", "decline setup #123"], ["/kill PIN", "KILL SWITCH"], ["/resume PIN", "release kill switch"]];

export function TelegramBotCard() {
  const { can } = useAuth();
  const { data, reload } = useApi("/tgbot");
  const [run, busy] = useAction();
  const [pin, setPin] = useState("");
  const [ask, setAsk] = useState(false);
  if (!data) return null;
  const edit = can("system.settings");
  return (
    <Card title="🤖 Telegram bot — control the app from your phone" actions={<Badge tone={data.enabled ? "good" : "muted"}>{data.enabled ? "on" : "off"}</Badge>}>
      {!data.telegram_ready && <div className="alert warn small">Connect Telegram above first (token + chat id).</div>}
      <div className="row gap wrap">
        <Switch on={data.enabled} disabled={!edit || busy || !data.telegram_ready} label="Answer my commands in Telegram"
          onChange={(v) => run(() => api.put("/tgbot", { enabled: v }), v ? "Bot on — send /help to your bot" : "Bot off").then(reload)} />
        <Badge tone={data.has_pin ? "good" : "warn"}>{data.has_pin ? "PIN set" : "no PIN yet"}</Badge>
      </div>
      <p className="small muted">Only your chat is answered. Risky commands need your PIN; the bot deletes that message right away. 5 wrong PINs lock PIN commands for 15 minutes.
        It takes up to a minute after switching on.</p>
      {edit && <div className="row gap wrap" style={{ alignItems: "flex-end" }}>
        <Field label={data.has_pin ? "Change PIN (4–8 digits)" : "Set PIN (4–8 digits)"}><input inputMode="numeric" maxLength={8} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} placeholder="e.g. 4826" /></Field>
        <button className="btn sm" disabled={pin.length < 4} onClick={() => setAsk(true)}>Save PIN</button>
      </div>}
      <div className="cmd-grid">{CMDS.map(([c, d]) => <div key={c}><code>{c}</code><span className="tiny muted">{d}</span></div>)}</div>
      {ask && <PasswordPrompt title="Confirm with your app password" onClose={() => setAsk(false)}
        onSubmit={(password) => run(() => api.put("/tgbot", { pin, password }), "PIN saved").then(() => { setPin(""); reload(); })} />}
    </Card>
  );
}

export function BackupCard() {
  const { data, reload } = useApi("/backup");
  const [run, busy] = useAction();
  const [pw, setPw] = useState("");
  const [ask, setAsk] = useState(false);
  if (!data) return null;
  const s = data.status || {};
  return (
    <Card title="💾 Backups" actions={<Badge tone={s.last_local_at ? "good" : "warn"}>{s.last_local_at ? "running" : "not seen yet"}</Badge>}>
      <p className="small muted">The server saves the whole database every night at 02:30 UTC and keeps 14 days. If the server itself is lost, those copies are lost too —
        so switch on the encrypted copy to your Telegram (only you can open it, with the backup password).</p>
      <div className="kv small">
        <div><span>Last server backup</span><b>{s.last_local_at ? `${fmt.dt(s.last_local_at)} · ${Math.round((s.last_local_bytes || 0) / 1024)} KB` : "—"}</b></div>
        <div><span>Last off-server copy (Telegram)</span><b>{s.last_offsite_at ? fmt.dt(s.last_offsite_at) : "—"}</b></div>
        {s.last_offsite_error && <div><span>Problem</span><b className="neg">{s.last_offsite_error}</b></div>}
      </div>
      <div className="row gap wrap" style={{ alignItems: "flex-end", marginTop: 10 }}>
        <Field label={data.has_password ? "Change backup password" : "Backup password (10+ characters — write it down!)"}><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        <button className="btn sm" disabled={pw.length < 10} onClick={() => setAsk(true)}>Save password</button>
        <Switch on={data.offsite_telegram} disabled={busy || !data.has_password} label="Send encrypted copy to my Telegram nightly"
          onChange={(v) => run(() => api.put("/backup", { offsite_telegram: v }), "Saved").then(reload)} />
      </div>
      <details className="small" style={{ marginTop: 8 }}><summary>How to restore</summary><p className="muted">{data.restore}</p></details>
      {ask && <PasswordPrompt title="Confirm with your app password" onClose={() => setAsk(false)}
        onSubmit={(password) => run(() => api.put("/backup", { backup_password: pw, password }), "Backup password saved").then(() => { setPw(""); reload(); })} />}
    </Card>
  );
}

export function ExplainChart({ symbol, tf }) {
  const [res, setRes] = useState(null);
  const [run, busy] = useAction();
  return (
    <>
      <button className="btn sm ghost" data-tour="explain" disabled={busy} onClick={() => { setRes({ loading: true }); run(() => api.post("/intel/chart-explain", { symbol, tf })).then(setRes).catch(() => setRes(null)); }}>
        🤖 Explain this chart
      </button>
      {res && (
        <Modal title={`What is ${symbol} doing? (${tf})`} onClose={() => setRes(null)} wide>
          {res.loading ? <p className="muted">Reading the chart…</p> : <div className="brief">{res.explanation}</div>}
          <p className="tiny muted">Explains the app's measured data in plain words. Not a prediction and not a trade signal.</p>
        </Modal>
      )}
    </>
  );
}

export function SecurityCard() {
  const { data, reload } = useApi("/security/checklist");
  if (!data) return null;
  return (
    <Card title={`🛡 Security checklist — ${data.score}/${data.total}`} actions={<button className="btn sm ghost" onClick={reload}>Re-check</button>}>
      <ul className="sec-list">
        {data.items.map((i) => (
          <li key={i.key} className={i.ok ? "ok" : "bad"}>
            <span>{i.ok ? "✓" : "✕"}</span>
            <div><b className="small">{i.label}</b><div className="tiny muted">{i.detail} {i.link && !i.ok && <a href={i.link}>Fix →</a>}</div></div>
          </li>
        ))}
      </ul>
      <p className="tiny muted">{data.server_check}</p>
    </Card>
  );
}
