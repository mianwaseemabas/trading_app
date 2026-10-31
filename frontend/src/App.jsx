import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, setCsrf, setUnauthorizedHandler } from "./api.js";
import { Badge, Field, modeLabel, modeTone, useApi, useToast } from "./components.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Market from "./pages/Market.jsx";
import Signals from "./pages/Signals.jsx";
import Trades from "./pages/Trades.jsx";
import Backtest from "./pages/Backtest.jsx";
import RiskStrategy from "./pages/RiskStrategy.jsx";
import Users from "./pages/Users.jsx";
import Audit from "./pages/Audit.jsx";
import Settings from "./pages/Settings.jsx";
import Account, { TwoFactorSetup } from "./pages/Account.jsx";
import Live from "./pages/Live.jsx";
import Brokers from "./pages/Brokers.jsx";
import Assistant from "./pages/Assistant.jsx";
import Guide from "./pages/Guide.jsx";
import News from "./pages/News.jsx";
import Overview from "./pages/Overview.jsx";
import Setups from "./pages/Setups.jsx";
import Smc from "./pages/Smc.jsx";
import Terminal from "./pages/Terminal.jsx";
import Alerts from "./pages/Alerts.jsx";

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

const ROUTES = [
  { k: "terminal", label: "Terminal", icon: "◈", perm: "market.view", el: Terminal, flush: true },
  { k: "dashboard", label: "Dashboard", icon: "◧", perm: "dashboard.view", el: Dashboard },
  { k: "overview", label: "Market overview", icon: "◎", perm: "market.view", el: Overview },
  { k: "alerts", label: "Alerts", icon: "🔔", perm: "dashboard.view", el: Alerts },
  { k: "setups", label: "Setups to approve", icon: "✓", perm: "signals.view", el: Setups },
  { k: "live", label: "Live trading", icon: "●", perm: "trades.view", el: Live },
  { k: "news", label: "News", icon: "◉", perm: "market.view", el: News },
  { k: "smc", label: "SMC scanner", icon: "⌖", perm: "market.view", el: Smc },
  { k: "market", label: "Market", icon: "◫", perm: "market.view", el: Market },
  { k: "signals", label: "Signals", icon: "◇", perm: "signals.view", el: Signals },
  { k: "trades", label: "Trades", icon: "⇅", perm: "trades.view", el: Trades },
  { k: "backtest", label: "Backtest", icon: "↺", perm: "backtest.run", el: Backtest },
  { k: "risk", label: "Risk & Strategy", icon: "⛨", perm: "dashboard.view", el: RiskStrategy },
  { k: "brokers", label: "Brokers", icon: "⇄", perm: "dashboard.view", el: Brokers },
  { k: "assistant", label: "AI Assistant", icon: "✦", perm: "ai.use", el: Assistant },
  { k: "guide", label: "Guide", icon: "?", perm: null, el: Guide },
  { k: "users", label: "Users", icon: "☰", perm: "users.manage", el: Users },
  { k: "audit", label: "Audit & Events", icon: "⌘", perm: "audit.view", el: Audit },
  { k: "settings", label: "System", icon: "⚙", perm: "system.settings", el: Settings },
  { k: "account", label: "My account", icon: "◉", perm: null, el: Account },
];

function useHashRoute() {
  const get = () => (window.location.hash.replace(/^#\/?/, "") || "terminal").split("?")[0];
  const [r, setR] = useState(get);
  useEffect(() => {
    const h = () => setR(get());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  return r;
}

function Login({ onDone }) {
  const [step, setStep] = useState("password");
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      if (step === "password") {
        const r = await api.post("/auth/login", { username, password });
        if (r.status === "2fa_required") {
          setCsrf(r.csrf_token);
          setStep("2fa");
        } else onDone(r.user);
      } else {
        const r = await api.post("/auth/2fa/verify", { code });
        onDone(r.user);
      }
    } catch (e2) {
      setErr(e2.message);
      if (e2.status === 401 && step === "2fa" && /expired|authenticated/i.test(e2.message)) setStep("password");
    } finally {
      setBusy(false);
      setP("");
    }
  };
  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={submit}>
        <div className="brand big"><span className="logo">Q</span><span>Qivynex<small className="brand-sub">Trading Intelligence Platform</small></span></div>
        <p className="muted small">Private system. Accounts are created by the administrator only.</p>
        {step === "password" ? (
          <>
            <Field label="Username"><input autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} /></Field>
            <Field label="Password"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} /></Field>
          </>
        ) : (
          <Field label="Authenticator code" hint="6-digit code from your authenticator app">
            <input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </Field>
        )}
        {err && <div className="alert bad">{err}</div>}
        <button className="btn primary block" disabled={busy || (step === "password" ? !username || !password : code.length !== 6)}>
          {busy ? "Checking…" : step === "password" ? "Sign in" : "Verify"}
        </button>
      </form>
    </div>
  );
}

function ForcePasswordChange({ user, onDone }) {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={async (e) => {
        e.preventDefault();
        setErr("");
        if (n1 !== n2) return setErr("New passwords do not match");
        try {
          const r = await api.post("/auth/change-password", { current_password: cur, new_password: n1 });
          toast("Password changed");
          onDone(r.user);
        } catch (e2) { setErr(e2.message); }
      }}>
        <h2>Set a new password</h2>
        <p className="muted small">Welcome {user.full_name || user.username}. You must set your own password before continuing. Use at least 12 characters with upper and lower case letters, a digit and a symbol.</p>
        <Field label="Current (temporary) password"><input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password"><input type="password" autoComplete="new-password" value={n1} onChange={(e) => setN1(e.target.value)} /></Field>
        <Field label="Repeat new password"><input type="password" autoComplete="new-password" value={n2} onChange={(e) => setN2(e.target.value)} /></Field>
        {err && <div className="alert bad">{err}</div>}
        <button className="btn primary block" disabled={!cur || !n1}>Save password</button>
      </form>
    </div>
  );
}

function Bell() {
  const { data } = useApi("/alerts/count", { interval: 15000 });
  const n = data?.unread || 0;
  return <a href="#/alerts" className={`bell ${n ? "has" : ""}`} title={`${n} unread alerts`}>🔔{n > 0 && <span>{n > 99 ? "99+" : n}</span>}</a>;
}

function usePrivacy() {
  const [on, setOn] = useState(() => { try { return localStorage.getItem("privacy") === "1"; } catch { return false; } });
  useEffect(() => {
    document.body.classList.toggle("privacy", on);
    try { localStorage.setItem("privacy", on ? "1" : "0"); } catch { /* ignore */ }
  }, [on]);
  return [on, () => setOn(!on)];
}

function TopBar({ user, logout }) {
  const can = (p) => user.permissions.includes(p);
  const [privacy, togglePrivacy] = usePrivacy();
  const { data } = useApi(can("dashboard.view") ? "/system/status" : null, { interval: 10000 });
  const tr = data?.trading;
  return (
    <header className="topbar">
      <div className="row gap">
        {tr && <Badge tone={modeTone(tr.mode)}>Mode: {modeLabel(tr.mode)}</Badge>}
        {tr?.kill_switch && <Badge tone="bad">KILL SWITCH ON</Badge>}
        {tr?.locked && <Badge tone="bad" title={tr.lock_reason}>LOCKED</Badge>}
        {data?.data?.source === "simulator" && <Badge tone="warn" title="Synthetic data for testing only">SIMULATED DATA</Badge>}
        {data && data.reconciliation && !data.reconciliation.ok && <Badge tone="bad" title={data.reconciliation.detail}>RECONCILIATION</Badge>}
      </div>
      <div className="row gap">
        {can("dashboard.view") && <Bell />}
        <button className="btn ghost sm" title="Hide personal and account details (for screenshots)" onClick={togglePrivacy}>{privacy ? "🙈 private" : "👁 details"}</button>
        <span className="muted small pii">{user.full_name || user.username}{user.is_admin ? " · admin" : ""}</span>
        <button className="btn ghost sm" onClick={logout}>Sign out</button>
      </div>
    </header>
  );
}

export default function App() {
  const [user, setUser] = useState(undefined);
  const route = useHashRoute();
  const toast = useToast();

  const applyUser = useCallback((u) => {
    if (u?.csrf_token) setCsrf(u.csrf_token);
    setUser(u);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api.get("/auth/me").then(applyUser).catch(() => setUser(null));
  }, [applyUser]);

  const logout = async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    setCsrf(null);
    setUser(null);
    toast("Signed out");
  };

  if (user === undefined) return <div className="center muted">Loading…</div>;
  if (user === null) return <Login onDone={applyUser} />;
  if (user.must_change_password) return <ForcePasswordChange user={user} onDone={applyUser} />;
  if (user.require_2fa && !user.totp_enabled) {
    return (
      <div className="login-wrap">
        <div className="login card wide">
          <h2>Two-factor authentication required</h2>
          <p className="muted small">Your administrator requires 2FA for your account. Set it up to continue.</p>
          <TwoFactorSetup onDone={applyUser} />
          <button className="btn ghost sm" onClick={logout}>Sign out</button>
        </div>
      </div>
    );
  }

  const allowed = ROUTES.filter((r) => !r.perm || user.permissions.includes(r.perm));
  const current = allowed.find((r) => r.k === route) || allowed[0];
  const Page = current.el;

  return (
    <AuthCtx.Provider value={{ user, setUser: applyUser, can: (p) => user.permissions.includes(p) }}>
      <div className={`shell ${current.flush ? "compact" : ""}`}>
        <nav className="side">
          <div className="brand"><span className="logo">Q</span><span>Qivynex<small className="brand-sub">Intelligence Platform</small></span></div>
          {allowed.map((r) => (
            <a key={r.k} href={`#/${r.k}`} title={r.label} className={r.k === current.k ? "nav on" : "nav"}>
              <span className="nav-i">{r.icon}</span><span className="nav-t">{r.label}</span>
            </a>
          ))}
          <div className="side-foot muted small">Automated trading can lose money. Demo first.</div>
        </nav>
        <main className="main">
          <TopBar user={user} logout={logout} />
          <div className={`page ${current.flush ? "flush" : ""}`}>
            {!current.flush && <h1>{current.label}</h1>}
            <Page />
          </div>
        </main>
      </div>
    </AuthCtx.Provider>
  );
}
