import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, PasswordPrompt, useAction, useApi } from "../components.jsx";

const QUICK = [
  "Explain my dashboard right now in simple words.",
  "Explain the most recent signal: why BUY/SELL, and why was it traded or rejected?",
  "Explain my open positions and what could happen next.",
  "Explain my last backtest results. Is the strategy good enough for demo trading?",
  "Teach me: what are EMA, RSI, MACD and ATR, with a simple example?",
  "What is a stop-loss and how does the app size my trades from my risk %?",
  "Give me a 5-minute daily routine to monitor this app safely.",
];

function Settings({ s, reload }) {
  const [run] = useAction();
  const [v, setV] = useState({ provider: s.provider, model: s.model, api_key: "", enabled: s.enabled, share_data: s.share_data });
  const [prompt, setPrompt] = useState(false);
  const hint = s.providers[v.provider]?.model_hint;
  return (
    <Card title="AI connection" actions={<Badge tone={s.enabled ? "ok" : "muted"}>{s.enabled ? "Enabled" : "Off"}</Badge>}>
      <p className="muted small">Use your own OpenAI or Anthropic (Claude) API key. The key is encrypted on your server. The assistant can only <b>explain and teach</b>: it cannot place or close trades or change settings.</p>
      <div className="grid form">
        <Field label="Provider">
          <select value={v.provider} onChange={(e) => setV({ ...v, provider: e.target.value })}>
            <option value="none">None</option>
            {Object.entries(s.providers).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Model" hint={hint}><input value={v.model} onChange={(e) => setV({ ...v, model: e.target.value })} placeholder={hint} /></Field>
        <Field label="API key" hint={s.api_key_set ? "Saved ✓ — leave empty to keep" : "Not set"}>
          <input type="password" autoComplete="off" value={v.api_key} placeholder={s.api_key_set ? "••••••••" : ""} onChange={(e) => setV({ ...v, api_key: e.target.value })} />
        </Field>
      </div>
      <div className="row gap wrap">
        <label className="check"><input type="checkbox" checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} /> Enable assistant</label>
        <label className="check"><input type="checkbox" checked={v.share_data} onChange={(e) => setV({ ...v, share_data: e.target.checked })} /> Share live app data (account, positions, signals, backtests) with the AI so it can explain them</label>
      </div>
      <div className="row end"><button className="btn primary" onClick={() => setPrompt(true)}>Save</button></div>
      {prompt && <PasswordPrompt title="Save AI settings" onClose={() => setPrompt(false)}
        onSubmit={(password) => run(() => api.put("/ai/settings", { ...v, api_key: v.api_key || null, password }), "AI settings saved").then(reload)} />}
    </Card>
  );
}

export default function Assistant() {
  const { can } = useAuth();
  const { data: s, reload } = useApi("/ai/settings");
  const [msgs, setMsgs] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem("qt_ai_chat") || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [run, busy] = useAction();
  const end = useRef(null);
  useEffect(() => {
    try { sessionStorage.setItem("qt_ai_chat", JSON.stringify(msgs.slice(-30))); } catch { /* ignore */ }
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);
  useEffect(() => {
    const q = new URLSearchParams(window.location.hash.split("?")[1] || "").get("q");
    if (q && s?.enabled) { send(q); window.location.hash = "#/assistant"; }
  }, [s]); // eslint-disable-line
  const send = (q) => {
    const content = (q ?? text).trim();
    if (!content) return;
    const next = [...msgs, { role: "user", content }];
    setMsgs(next);
    setText("");
    run(() => api.post("/ai/chat", { messages: next.slice(-20) }))
      .then((r) => setMsgs((m) => [...m, { role: "assistant", content: r.reply }]))
      .catch((e) => setMsgs((m) => [...m, { role: "assistant", content: `⚠️ ${e.message}`, error: true }]));
  };
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      {can("system.settings") && <Settings s={s} reload={reload} />}
      <Card title="Trading coach" actions={msgs.length > 0 && <button className="btn sm ghost" onClick={() => setMsgs([])}>New chat</button>}>
        {!s.enabled ? (
          <p className="muted">The assistant is off. {can("system.settings") ? "Choose a provider, model and API key above and enable it." : "Ask an administrator to enable it."}</p>
        ) : (
          <>
            {msgs.length === 0 && (
              <div className="quick">
                <p className="muted small">Ask anything in English or Urdu, or start with one of these:</p>
                {QUICK.map((q) => <button key={q} className="btn sm ghost" onClick={() => send(q)}>{q}</button>)}
              </div>
            )}
            <div className="chat">
              {msgs.map((m, i) => (
                <div key={i} className={`msg ${m.role} ${m.error ? "err" : ""}`}>{m.content}</div>
              ))}
              {busy && <div className="msg assistant muted">Thinking…</div>}
              <div ref={end} />
            </div>
            <form className="row gap" onSubmit={(e) => { e.preventDefault(); send(); }}>
              <input className="grow" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about your trades, signals, risk or any trading concept…" />
              <button className="btn primary" disabled={busy || !text.trim()}>Send</button>
            </form>
            <p className="muted small">Education only, not financial advice. The AI can be wrong; the risk engine always has the final say.</p>
          </>
        )}
      </Card>
    </div>
  );
}
