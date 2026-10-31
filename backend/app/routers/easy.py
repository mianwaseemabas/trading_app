"""Easy mode: per-user view preferences, the Home summary, and the Autopilot (practice) wizard."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, current_user_any, require
from ..models import Alert, DataIncident, NewsEvent, Position, Signal, SignalApproval, User, utcnow

router = APIRouter(prefix="/api", tags=["easy"])
DEFAULT_PREFS = {"mode": "easy", "explain": True}


# ------------------------------------------------------------------ preferences
def prefs_of(db: Session, user: User) -> dict:
    return {**DEFAULT_PREFS, **(state.get(db, "ui_prefs") or {}).get(str(user.id), {})}


@router.get("/me/prefs")
def get_prefs(user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    return prefs_of(db, user)


class PrefsIn(BaseModel):
    mode: str | None = None
    explain: bool | None = None


@router.put("/me/prefs")
def put_prefs(body: PrefsIn, user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    if body.mode is not None and body.mode not in ("easy", "advanced"):
        raise HTTPException(400, "mode must be easy or advanced")
    allp = dict(state.get_locked(db, "ui_prefs") or {})
    p = {**DEFAULT_PREFS, **allp.get(str(user.id), {})}
    if body.mode is not None:
        p["mode"] = body.mode
    if body.explain is not None:
        p["explain"] = body.explain
    allp[str(user.id)] = p
    state.put(db, "ui_prefs", allp, by=user.username)
    db.commit()
    return p


# ------------------------------------------------------------------ home
def _light(ok: bool | None, label: str, detail: str, link: str = "") -> dict:
    return {"state": "ok" if ok else ("off" if ok is None else "bad"), "label": label, "detail": detail, "link": link}


@router.get("/home")
def home(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    from ..services.automation import flags_of
    from ..services.intel import BROKER_NAME
    from ..services.livegate import evidence
    now = utcnow()
    tr = state.get(db, "trading")
    cc = state.get(db, "crypto")
    hb = state.get(db, "health")
    w_age = (now - datetime.fromisoformat(hb["worker_at"])).total_seconds() if hb.get("worker_at") else None
    incidents = db.scalars(select(DataIncident).where(DataIncident.ended_at.is_(None))).all()
    cst = state.get(db, "crypto_status")
    brokers = state.get(db, "brokers")
    cstat = state.get(db, "connector_status").get(brokers.get("active") or "", {})
    bk = state.get(db, "backup_status")
    tg = state.get(db, "alert_prefs").get("telegram", {})
    b_age = (now - datetime.fromisoformat(bk["last_local_at"])).total_seconds() / 3600 if bk.get("last_local_at") else None
    broker_on = tr["mode"] in ("MT5_DEMO", "MT5_LIVE")
    lights = [
        _light(w_age is not None and w_age < 180, "App engine", "running" if w_age is not None and w_age < 180 else "not responding — check the server"),
        _light(not incidents, "Prices", "all markets updating" if not incidents else f"{len(incidents)} problem(s): " + ", ".join(f"{i.symbol or ''} {i.kind.lower().replace('_', ' ')}".strip() for i in incidents[:3]), "#/dashboard"),
        _light((cstat.get("state") == "connected") if broker_on else None,
               "Broker" + (f" ({BROKER_NAME[brokers['active']]})" if brokers.get("active") in ("capital", "ctrader", "mt5") else ""),
               (cstat.get("state") or "unknown") if broker_on else "not used (practice mode)", "#/brokers"),
        _light(bool(cst.get("market_ok")) if cc.get("mode", "OFF") != "OFF" else None, f"Exchange ({cc.get('exchange', 'binance').capitalize()})",
               ("connected" if cst.get("market_ok") else (cst.get("market_error") or "no data"))[:90] if cc.get("mode", "OFF") != "OFF" else "crypto off", "#/brokers"),
        _light(b_age is not None and b_age < 36, "Backups", f"last {b_age:.0f} h ago" if b_age is not None else "not seen yet", "#/settings"),
        _light(bool(tg.get("bot_token_enc") and tg.get("chat_id")), "Telegram alerts", "connected" if tg.get("chat_id") else "not set up", "#/alerts"),
    ]
    # today
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    opened = db.scalars(select(Position).where(Position.opened_at >= day0)).all()
    closed = db.scalars(select(Position).where(Position.status == "CLOSED", Position.closed_at >= day0)).all()
    open_pos = db.scalars(select(Position).where(Position.status == "OPEN").order_by(Position.opened_at)).all()
    realized = round(sum(p.realized_pnl or 0 for p in closed), 2)
    unreal = round(sum(p.unrealized_pnl or 0 for p in open_pos), 2)
    # needs you
    needs = []
    waiting = db.execute(select(Signal, SignalApproval).join(SignalApproval, SignalApproval.signal_id == Signal.id)
                         .where(SignalApproval.status == "WAITING", SignalApproval.expires_at > now)).all()
    for s, a in waiting[:5]:
        needs.append({"kind": "approve", "text": f"Approve or decline: {s.symbol} {s.action} ({s.timeframe}) — expires {a.expires_at:%H:%M} UTC", "link": "#/setups"})
    if tr.get("kill_switch"):
        needs.append({"kind": "problem", "text": "Kill switch is ON — no new orders. Release it on Live trading when you are ready.", "link": "#/live"})
    if tr.get("locked"):
        needs.append({"kind": "problem", "text": f"Trading locked: {tr.get('lock_reason', '')}", "link": "#/live"})
    for l in lights:
        if l["state"] == "bad":
            needs.append({"kind": "problem", "text": f"{l['label']}: {l['detail']}", "link": l["link"] or "#/dashboard"})
    for p in open_pos:
        if not p.stop_loss:
            needs.append({"kind": "problem", "text": f"{p.symbol} position has NO stop-loss — set one now", "link": "#/live"})
    crit = db.scalars(select(Alert).where(Alert.read_at.is_(None), Alert.severity == "critical", Alert.at >= now - timedelta(hours=24))).all()
    if crit:
        needs.append({"kind": "problem", "text": f"{len(crit)} critical alert(s) unread", "link": "#/alerts"})
    news = db.scalars(select(NewsEvent).where(NewsEvent.impact == "High", NewsEvent.time >= now, NewsEvent.time <= now + timedelta(hours=12))
                      .order_by(NewsEvent.time).limit(4)).all()
    # week + readiness
    week = db.scalars(select(Position).where(Position.status == "CLOSED", Position.closed_at >= now - timedelta(days=7))).all()
    wnet = round(sum(p.realized_pnl or 0 for p in week), 2)
    wins = sum(1 for p in week if (p.realized_pnl or 0) > 0)
    ev = evidence(db, user)
    ap = state.get(db, "autopilot")
    flags = flags_of(state.get(db, "controls"))
    return {
        "lights": lights, "all_ok": all(l["state"] != "bad" for l in lights),
        "mode": tr["mode"], "crypto_mode": cc.get("mode", "OFF"),
        "autopilot": {"on": bool(ap.get("on")), "since": ap.get("started_at"), "accounts": [m for m, v in flags.items() if v]},
        "today": {"opened": len(opened), "closed": len(closed), "realized": realized, "open": len(open_pos), "unrealized": unreal,
                  "positions": [{"id": p.id, "symbol": p.symbol, "side": p.side, "mode": p.mode, "pnl": round(p.unrealized_pnl or 0, 2),
                                 "sl": p.stop_loss, "tp": p.take_profit, "open": p.open_price} for p in open_pos[:8]]},
        "needs": needs, "news": [{"time": e.time, "currency": e.currency, "title": e.title} for e in news],
        "week": {"trades": len(week), "net": wnet, "win_rate": round(100 * wins / len(week)) if week else None},
        "readiness": {"passed": sum(1 for v in ev.values() if v["passed"]), "total": len(ev),
                      "missing": [v["detail"] for v in ev.values() if not v["passed"]][:3]},
    }


# ------------------------------------------------------------------ autopilot
TEST_RISK = {"risk_per_trade_pct": 0.25, "daily_loss_pct": 1.0, "weekly_loss_pct": 3.0, "max_concurrent_positions": 3,
             "max_positions_per_symbol": 1, "max_trades_per_day": 10}
ALERT_TYPES = ["AUTO_TRADE", "ORDER_FILLED", "STOP_HIT", "TARGET_1_HIT", "APPROVAL_REQUIRED", "PROTECTION_FAILURE", "LOSS_LIMIT_REACHED",
               "KILL_SWITCH_ACTIVE", "HEALTH", "AI_BRIEF", "AI_COACH", "MARKET_NEWS"]


def _plan(db: Session) -> list[dict]:
    tr = state.get(db, "trading")
    cc = state.get(db, "crypto")
    ai = state.get(db, "ai")
    if tr["mode"] == "MT5_LIVE" or cc.get("mode") == "LIVE":
        raise HTTPException(400, "Autopilot is for practice only — the app is in a REAL-MONEY mode. Switch to practice/demo first.")
    accounts = ["PAPER"] + (["MT5_DEMO"] if tr["mode"] == "MT5_DEMO" else []) + \
        ({"PAPER": ["CRYPTO_PAPER"], "TEST": ["CRYPTO_TEST"]}.get(cc.get("mode"), []))
    risk = state.get(db, "risk")
    steps = [{"what": "Safe test limits", "detail": ", ".join(f"{k.replace('_', ' ')} {risk.get(k)} → {v}" for k, v in TEST_RISK.items() if risk.get(k) != v) or "already set"},
             {"what": "Auto-trade ON (practice/test only)", "detail": ", ".join(accounts) + " · real-money accounts stay OFF"},
             {"what": "Safety switches", "detail": "strategy trading on, news filter on"},
             {"what": "News automation", "detail": "news breakout on" + (", AI news filter on" if ai.get("enabled") else " (AI news filter needs the AI assistant)")},
             {"what": "Alerts to Telegram + phone", "detail": ", ".join(t.replace("_", " ").lower() for t in ALERT_TYPES[:6]) + " …"},
             {"what": "AI helpers", "detail": f"morning brief {state.get(db, 'ai_jobs').get('brief_time', '07:30')} and weekly coach (Sunday)"
              + ("" if ai.get("enabled") else " — plain summaries until the AI assistant is on")}]
    if tr["mode"] in ("OFF", "SIGNALS_ONLY"):
        steps.insert(0, {"what": "Trading mode → PRACTICE (PAPER)", "detail": f"currently {tr['mode']}"})
    return steps, accounts


@router.get("/autopilot")
def get_autopilot(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    ap = state.get(db, "autopilot")
    try:
        steps, accounts = _plan(db)
        blocked = ""
    except HTTPException as e:
        steps, accounts, blocked = [], [], e.detail
    return {"on": bool(ap.get("on")), "started_at": ap.get("started_at"), "plan": steps, "accounts": accounts, "blocked": blocked}


@router.post("/autopilot/start")
def start_autopilot(request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    steps, accounts = _plan(db)
    tr = state.get_locked(db, "trading")
    if tr["mode"] in ("OFF", "SIGNALS_ONLY"):
        tr["mode"] = "PAPER"
        state.put(db, "trading", tr, by=user.username)
    risk = state.get_locked(db, "risk")
    before = {k: risk.get(k) for k in TEST_RISK}
    risk.update(TEST_RISK)
    state.put(db, "risk", risk, by=user.username)
    c = state.get_locked(db, "controls")
    flags = {m: False for m in ("PAPER", "MT5_DEMO", "MT5_LIVE", "CRYPTO_PAPER", "CRYPTO_TEST", "CRYPTO_LIVE")}
    for m in accounts:
        flags[m] = True
    c.update(auto_execute=flags, auto_trading=True, news_filter=True, require_approval=False)
    state.put(db, "controls", c, by=user.username)
    ai = state.get(db, "ai")
    na = state.get_locked(db, "news_auto")
    na.update(breakout=True, ai_filter=bool(ai.get("enabled") and ai.get("api_key_enc")))
    state.put(db, "news_auto", na, by=user.username)
    p = state.get_locked(db, "alert_prefs")
    for ch in ("telegram", "push"):
        cur = p.setdefault("channels", {}).setdefault(ch, [])
        cur.extend(t for t in ALERT_TYPES if t not in cur)
    state.put(db, "alert_prefs", p, by=user.username)
    j = state.get_locked(db, "ai_jobs")
    j.update(brief_enabled=True, coach_enabled=True)
    state.put(db, "ai_jobs", j, by=user.username)
    state.put(db, "autopilot", {"on": True, "started_at": utcnow().isoformat(), "by": user.username, "before_risk": before, "accounts": accounts})
    audit(db, "autopilot.start", user=user, ip=client_ip(request), details={"accounts": accounts, "risk": TEST_RISK})
    from ..services.alerts import raise_alert
    raise_alert(db, "MODE_CHANGED", "🤖 Autopilot (practice) started", "Auto-trade on: " + ", ".join(accounts) + ". Real money stays off. "
                "Stop any time on Home.", dedup=f"autopilot:{utcnow().isoformat()}", link="#/home")
    db.commit()
    return get_autopilot(user, db)


@router.post("/autopilot/stop")
def stop_autopilot(request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    c = state.get_locked(db, "controls")
    c["auto_execute"] = {m: False for m in ("PAPER", "MT5_DEMO", "MT5_LIVE", "CRYPTO_PAPER", "CRYPTO_TEST", "CRYPTO_LIVE")}
    c["require_approval"] = True
    state.put(db, "controls", c, by=user.username)
    na = state.get_locked(db, "news_auto")
    na["breakout"] = False
    state.put(db, "news_auto", na, by=user.username)
    ap = state.get(db, "autopilot")
    ap.update(on=False, stopped_at=utcnow().isoformat())
    state.put(db, "autopilot", ap)
    audit(db, "autopilot.stop", user=user, ip=client_ip(request))
    db.commit()
    return get_autopilot(user, db)
