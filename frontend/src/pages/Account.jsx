import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Table, fmt, useAction, useApi, useToast } from "../components.jsx";

export function TwoFactorSetup({ onDone }) {
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [run, busy] = useAction();
  if (!setup) {
    return <button className="btn primary" disabled={busy} onClick={() => run(() => api.post("/auth/2fa/setup")).then(setSetup).catch(() => {})}>Set up authenticator app</button>;
  }
  const grouped = setup.secret.match(/.{1,4}/g).join(" ");
  return (
    <div className="stack">
      <ol className="small">
        <li>Open Google Authenticator, Microsoft Authenticator, 1Password or similar.</li>
        <li>Add an account → <b>Enter a setup key</b> (time-based) and type the key below. On a phone you can also tap the link.</li>
        <li>Enter the 6-digit code the app shows.</li>
      </ol>
      <div className="secret">{grouped}</div>
      <a className="small" href={setup.otpauth_uri}>Open in authenticator app</a>
      <Field label="6-digit code"><input inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} /></Field>
      <button className="btn primary" disabled={code.length !== 6 || busy} onClick={() => run(() => api.post("/auth/2fa/enable", { code }), "Two-factor authentication enabled").then((r) => onDone(r.user)).catch(() => {})}>Verify & enable</button>
    </div>
  );
}

export default function Account() {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const [run] = useAction();
  const { data: sessions, reload } = useApi("/auth/sessions");
  const [pw, setPw] = useState({ current_password: "", new_password: "", repeat: "" });
  const [dis, setDis] = useState({ password: "", code: "" });
  return (
    <div className="stack">
      <div className="grid two">
        <Card title="Profile">
          <div className="kv">
            <div><span className="muted">Username</span><span>{user.username}</span></div>
            <div><span className="muted">Name</span><span>{user.full_name || "—"}</span></div>
            <div><span className="muted">Role</span><span>{user.is_admin ? "Administrator" : "User"}</span></div>
            <div><span className="muted">Last sign-in</span><span>{fmt.dt(user.last_login_at)}</span></div>
          </div>
          <h4>Permissions</h4>
          <div className="row gap wrap">{user.permissions.map((p) => <Badge key={p} tone="info">{p}</Badge>)}</div>
        </Card>
        <Card title="Change password">
          <form onSubmit={(e) => {
            e.preventDefault();
            if (pw.new_password !== pw.repeat) return toast("Passwords do not match", "err");
            run(() => api.post("/auth/change-password", { current_password: pw.current_password, new_password: pw.new_password }), "Password changed. Other sessions were signed out.")
              .then((r) => { setUser(r.user); setPw({ current_password: "", new_password: "", repeat: "" }); reload(); }).catch(() => {});
          }}>
            <Field label="Current password"><input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
            <Field label="New password" hint="12+ chars, upper & lower case, digit, symbol"><input type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
            <Field label="Repeat new password"><input type="password" autoComplete="new-password" value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} /></Field>
            <button className="btn primary">Change password</button>
          </form>
        </Card>
      </div>
      <Card title="Two-factor authentication" actions={<Badge tone={user.totp_enabled ? "ok" : "warn"}>{user.totp_enabled ? "Enabled" : "Disabled"}</Badge>}>
        {user.totp_enabled ? (
          user.require_2fa ? <p className="muted">Required for your account by the administrator.</p> : (
            <div className="row gap end wrap">
              <Field label="Password"><input type="password" value={dis.password} onChange={(e) => setDis({ ...dis, password: e.target.value })} /></Field>
              <Field label="Current code"><input inputMode="numeric" maxLength={6} value={dis.code} onChange={(e) => setDis({ ...dis, code: e.target.value })} /></Field>
              <button className="btn" onClick={() => run(() => api.post("/auth/2fa/disable", dis), "2FA disabled").then((r) => setUser(r.user)).catch(() => {})}>Disable 2FA</button>
            </div>
          )
        ) : <TwoFactorSetup onDone={setUser} />}
      </Card>
      <Card title="Active sessions">
        <Table rows={sessions} cols={[
          { label: "Started", render: (s) => fmt.dt(s.created_at) }, { label: "Last active", render: (s) => fmt.dt(s.last_seen_at) },
          { label: "IP", k: "ip" }, { label: "Device", render: (s) => <span className="small trunc">{s.user_agent}</span> },
          { label: "", render: (s) => (s.current ? <Badge tone="ok">this session</Badge> : <button className="btn sm" onClick={() => run(() => api.post(`/auth/sessions/${s.id}/revoke`), "Session ended").then(reload)}>End</button>) },
        ]} />
      </Card>
    </div>
  );
}
