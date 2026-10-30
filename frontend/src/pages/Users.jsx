import React, { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, Field, Modal, Table, fmt, useAction, useApi } from "../components.jsx";

function PermissionPicker({ meta, value, onChange, disabled }) {
  return (
    <div className="stack">
      <div className="row gap wrap">
        <span className="muted small">Presets:</span>
        {Object.entries(meta.roles).map(([r, perms]) => (
          <button type="button" key={r} className="btn sm ghost" disabled={disabled} onClick={() => onChange(perms)}>{r}</button>
        ))}
      </div>
      <div className="perm-grid">
        {Object.entries(meta.permissions).map(([p, desc]) => (
          <label key={p} className="check perm">
            <input type="checkbox" disabled={disabled} checked={value.includes(p)} onChange={(e) => onChange(e.target.checked ? [...value, p] : value.filter((x) => x !== p))} />
            <span><b>{p}</b><br /><span className="muted small">{desc}</span></span>
          </label>
        ))}
      </div>
    </div>
  );
}

function SecretBox({ title, value, onClose }) {
  return (
    <Modal title={title} onClose={onClose}>
      <p>Share this temporary password securely. It is shown only once; the user must change it at first sign-in.</p>
      <div className="secret">{value}</div>
      <div className="row end gap">
        <button className="btn" onClick={() => navigator.clipboard?.writeText(value)}>Copy</button>
        <button className="btn primary" onClick={onClose}>Done</button>
      </div>
    </Modal>
  );
}

function UserEditor({ meta, user, onClose, onSaved }) {
  const { user: me } = useAuth();
  const isNew = !user;
  const [run, busy] = useAction();
  const [f, setF] = useState(user ? { ...user } : { username: "", full_name: "", email: "", password: "", is_admin: false, permissions: meta.roles.viewer, require_2fa: true });
  const save = () => {
    const req = isNew
      ? api.post("/users", { username: f.username, full_name: f.full_name, email: f.email, password: f.password || null, is_admin: f.is_admin, permissions: f.permissions, require_2fa: f.require_2fa })
      : api.patch(`/users/${user.id}`, { full_name: f.full_name, email: f.email, is_admin: f.is_admin, permissions: f.permissions, require_2fa: f.require_2fa, is_active: f.is_active });
    run(() => req, isNew ? "User created" : "User updated").then(onSaved).catch(() => {});
  };
  return (
    <Modal title={isNew ? "Create user" : `Edit ${user.username}`} onClose={onClose} wide>
      <div className="grid form">
        <Field label="Username" hint="a-z 0-9 . _ -"><input disabled={!isNew} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} /></Field>
        <Field label="Full name"><input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Email"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        {isNew && <Field label="Initial password" hint="leave empty to generate a strong temporary password"><input type="text" autoComplete="off" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>}
      </div>
      <div className="row gap wrap">
        {me.is_admin && <label className="check"><input type="checkbox" checked={f.is_admin} disabled={!isNew && user.id === me.id} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} /> Administrator (all permissions)</label>}
        <label className="check"><input type="checkbox" checked={f.require_2fa} onChange={(e) => setF({ ...f, require_2fa: e.target.checked })} /> Require 2FA</label>
        {!isNew && <label className="check"><input type="checkbox" checked={f.is_active} disabled={user.id === me.id} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>}
      </div>
      <h4>Permissions</h4>
      <PermissionPicker meta={meta} value={f.permissions} onChange={(p) => setF({ ...f, permissions: p })} disabled={f.is_admin} />
      <div className="row end gap">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || (isNew && f.username.length < 3)} onClick={save}>{isNew ? "Create" : "Save"}</button>
      </div>
    </Modal>
  );
}

export default function Users() {
  const { user: me } = useAuth();
  const { data: users, reload } = useApi("/users");
  const { data: meta } = useApi("/users/meta");
  const [edit, setEdit] = useState(null);
  const [secret, setSecret] = useState(null);
  const [run] = useAction();
  const act = (u, path, msg, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    run(() => api.post(`/users/${u.id}/${path}`, path === "reset-password" ? {} : undefined), msg).then((r) => { if (r?.temporary_password) setSecret(r.temporary_password); reload(); }).catch(() => {});
  };
  return (
    <Card title="Users" actions={<button className="btn primary sm" disabled={!meta} onClick={() => setEdit("new")}>+ Create user</button>}>
      <p className="muted small">There is no public sign-up. Only users with the <b>users.manage</b> permission can create accounts. New users must change their password on first sign-in.</p>
      <Table rows={users} cols={[
        { label: "User", render: (u) => <><b>{u.username}</b><div className="muted small">{u.full_name}</div></> },
        { label: "Role", render: (u) => (u.is_admin ? <Badge tone="warn">admin</Badge> : <span className="small">{u.permissions.length} permission(s)</span>) },
        { label: "Status", render: (u) => <div className="row gap wrap">
          <Badge tone={u.is_active ? "ok" : "muted"}>{u.is_active ? "active" : "disabled"}</Badge>
          {u.locked && <Badge tone="bad">locked</Badge>}
          {u.must_change_password && <Badge tone="info">pw change due</Badge>}
          <Badge tone={u.totp_enabled ? "ok" : u.require_2fa ? "warn" : "muted"}>{u.totp_enabled ? "2FA on" : u.require_2fa ? "2FA pending" : "no 2FA"}</Badge>
        </div> },
        { label: "Last sign-in", render: (u) => <>{fmt.dt(u.last_login_at)}<div className="muted small">{u.last_login_ip}</div></> },
        { label: "Created", render: (u) => <>{fmt.dt(u.created_at)}<div className="muted small">by {u.created_by}</div></> },
        { label: "", render: (u) => (
          <div className="row gap wrap">
            <button className="btn sm" onClick={() => setEdit(u)}>Edit</button>
            <button className="btn sm ghost" onClick={() => act(u, "reset-password", "Password reset", `Reset password for ${u.username}? All their sessions end.`)}>Reset pw</button>
            {u.locked && <button className="btn sm ghost" onClick={() => act(u, "unlock", "Unlocked")}>Unlock</button>}
            {u.totp_enabled && <button className="btn sm ghost" onClick={() => act(u, "reset-2fa", "2FA reset", `Remove 2FA for ${u.username}?`)}>Reset 2FA</button>}
            <button className="btn sm ghost" onClick={() => act(u, "revoke-sessions", "Sessions revoked")}>Sign out</button>
            {u.id !== me.id && <button className="btn sm ghost danger-t" onClick={() => window.confirm(`Delete ${u.username} permanently? (Disabling is usually better.)`) && run(() => api.del(`/users/${u.id}`), "User deleted").then(reload).catch(() => {})}>Delete</button>}
          </div>
        ) },
      ]} />
      {edit && meta && (
        <UserEditor meta={meta} user={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(r) => { setEdit(null); reload(); if (r?.temporary_password) setSecret(r.temporary_password); }} />
      )}
      {secret && <SecretBox title="Temporary password" value={secret} onClose={() => setSecret(null)} />}
    </Card>
  );
}
