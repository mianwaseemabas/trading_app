import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, fmt, useAction, useApi } from "../components.jsx";

const SEV = { info: "info", warning: "warn", critical: "bad" };

function Preferences() {
  const { can } = useAuth();
  const { data, reload } = useApi("/alerts/preferences");
  const [run, busy] = useAction();
  const [ch, setCh] = useState(null);
  const [tg, setTg] = useState({ token: "", chat: "" });
  const [em, setEm] = useState(null);
  const [test, setTest] = useState(null);
  useEffect(() => {
    if (data) {
      setCh(data.channels);
      setTg({ token: "", chat: data.telegram.chat_id || "" });
      setEm({ host: "", port: 587, username: "", from: "", to: "", starttls: true, password: "", ...data.email });
    }
  }, [data]);
  if (!data || !ch || !em) return null;
  const editable = can("system.settings");
  const toggle = (c, t) => setCh({ ...ch, [c]: (ch[c] || []).includes(t) ? ch[c].filter((x) => x !== t) : [...(ch[c] || []), t] });
  const save = () => run(() => api.post("/alerts/preferences", { channels: ch, telegram_token: tg.token || null, telegram_chat_id: tg.chat,
    email: { ...em, port: Number(em.port) || 587 } }), "Alert preferences saved").then(reload).catch(() => {});
  return (
    <Card title="Where alerts are sent" actions={editable && <button className="btn primary sm" disabled={busy} onClick={save}>Save</button>}>
      <p className="small muted">Every alert always appears here in the app. Choose which ones are also sent to Telegram or email. Alerts are information only — they never place orders.</p>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Alert</th><th>Telegram</th><th>Email</th></tr></thead>
          <tbody>{Object.entries(data.types).map(([t, d]) => (
            <tr key={t}><td><b className="small">{t.replace(/_/g, " ")}</b><div className="tiny muted">{d}</div></td>
              {["telegram", "email"].map((c) => <td key={c}><input type="checkbox" disabled={!editable} checked={(ch[c] || []).includes(t)} onChange={() => toggle(c, t)} /></td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <div className="grid two" style={{ marginTop: 12 }}>
        <div>
          <h4>Telegram</h4>
          <p className="tiny muted">Create a bot with @BotFather, send it a message, then get your chat id (e.g. from @userinfobot).</p>
          <Field label={`Bot token ${data.telegram.has_token ? "(saved — leave empty to keep)" : ""}`}><input type="password" disabled={!editable} value={tg.token} onChange={(e) => setTg({ ...tg, token: e.target.value })} /></Field>
          <Field label="Chat id"><input disabled={!editable} value={tg.chat} onChange={(e) => setTg({ ...tg, chat: e.target.value })} /></Field>
          {editable && <button className="btn sm" onClick={() => run(() => api.post("/alerts/test", { channel: "telegram" })).then(setTest).catch(() => {})}>Send test</button>}
        </div>
        <div>
          <h4>Email (SMTP)</h4>
          <div className="grid two">
            <Field label="SMTP server"><input disabled={!editable} value={em.host} onChange={(e) => setEm({ ...em, host: e.target.value })} placeholder="smtp.office365.com" /></Field>
            <Field label="Port"><input disabled={!editable} value={em.port} onChange={(e) => setEm({ ...em, port: e.target.value })} /></Field>
            <Field label="Username"><input disabled={!editable} value={em.username} onChange={(e) => setEm({ ...em, username: e.target.value })} /></Field>
            <Field label={`Password ${em.has_password ? "(saved)" : ""}`}><input type="password" disabled={!editable} value={em.password || ""} onChange={(e) => setEm({ ...em, password: e.target.value })} /></Field>
            <Field label="From"><input disabled={!editable} value={em.from} onChange={(e) => setEm({ ...em, from: e.target.value })} /></Field>
            <Field label="To"><input disabled={!editable} value={em.to} onChange={(e) => setEm({ ...em, to: e.target.value })} /></Field>
          </div>
          {editable && <button className="btn sm" onClick={() => run(() => api.post("/alerts/test", { channel: "email" })).then(setTest).catch(() => {})}>Send test</button>}
        </div>
      </div>
      {test && <div className={`alert ${test.ok ? "ok" : "bad"} small`}>{test.ok ? "Delivered." : `Not delivered: ${test.detail}`}</div>}
    </Card>
  );
}

export default function Alerts() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const { data, reload } = useApi(`/alerts?limit=200${unreadOnly ? "&unread=true" : ""}`, { interval: 15000, deps: [unreadOnly] });
  const [run] = useAction();
  const [open, setOpen] = useState(null);
  return (
    <div className="stack">
      <Card title={`Inbox (${data?.unread ?? 0} unread)`} actions={
        <>
          <label className="check small"><input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} /> unread only</label>
          <button className="btn sm" onClick={() => run(() => api.post("/alerts/read", { all: true }), "All marked read").then(reload)}>Mark all read</button>
        </>
      }>
        {!data?.alerts?.length && <p className="muted small">No alerts yet. You'll see qualified setups, approvals, fills, stops/targets, loss limits and data problems here.</p>}
        <div className="alert-list">
          {(data?.alerts || []).map((a) => (
            <div key={a.id} className={`alert-row ${a.read ? "" : "unread"}`} onClick={() => { setOpen(open === a.id ? null : a.id); if (!a.read) api.post("/alerts/read", { ids: [a.id] }).then(reload); }}>
              <div className="row between gap wrap">
                <span className="row gap"><Badge tone={SEV[a.severity]}>{a.type.replace(/_/g, " ")}</Badge><b>{a.title}</b></span>
                <span className="small muted">{fmt.dt(a.at)}</span>
              </div>
              {open === a.id && (
                <div style={{ marginTop: 6 }}>
                  <pre className="alert-body">{a.body}</pre>
                  <div className="row gap wrap tiny muted">
                    {a.deliveries.map((d, i) => <span key={i}>{d.channel}: {d.status}{d.attempts > 1 ? ` ×${d.attempts}` : ""}{d.status !== "sent" ? ` (${d.detail})` : ""}</span>)}
                    {a.link && <a href={a.link}>Open →</a>}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
      <Preferences />
    </div>
  );
}
