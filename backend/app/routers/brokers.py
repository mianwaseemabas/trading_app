"""Broker connections managed from the dashboard (no VPS commands needed)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..services import livegate
from ..models import Position, User, utcnow
from ..secretbox import encrypt, mask
from ..security import user_has, verify_password
from ..services.notifier import notify

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

CONNECTORS = {
    "capital": {"name": "Capital.com", "kind": "built-in", "runs_on": "this server"},
    "ctrader": {"name": "cTrader", "kind": "built-in", "runs_on": "this server"},
    "mt5": {"name": "MetaTrader 5", "kind": "external", "runs_on": "your Windows PC/VPS (bridge/)"},
}
SECRET_FIELDS = {
    "capital": {"api_key": "api_key_enc", "api_password": "api_password_enc"},
    "ctrader": {"client_secret": "client_secret_enc", "access_token": "access_token_enc", "refresh_token": "refresh_token_enc"},
}
PLAIN_FIELDS = {"capital": {"identifier", "account_id", "environment"}, "ctrader": {"client_id", "account_login"}}


def _confirm(user: User, password: str | None):
    if not password or not verify_password(password, user.password_hash):
        raise HTTPException(403, "Password confirmation failed")


def configured(name: str, cfg: dict) -> bool:
    c = cfg.get(name, {})
    if name == "capital":
        return bool(c.get("identifier") and c.get("api_key_enc") and c.get("api_password_enc"))
    if name == "ctrader":
        return bool(c.get("client_id") and c.get("client_secret_enc") and c.get("access_token_enc"))
    return True  # mt5 bridge is configured on the Windows side


def public_view(db: Session) -> dict:
    cfg = state.get(db, "brokers")
    status = state.get(db, "connector_status")
    bridge = state.get(db, "bridge")
    out = {"active": cfg["active"], "allow_real": cfg["allow_real"], "max_volume": cfg["max_volume"],
           "max_spread_points": cfg["max_spread_points"], "connectors": {}}
    for name, meta in CONNECTORS.items():
        c = cfg.get(name, {}) if name != "mt5" else {}
        fields = {}
        for k in PLAIN_FIELDS.get(name, ()):
            fields[k] = c.get(k, "")
        for k, enc in SECRET_FIELDS.get(name, {}).items():
            fields[k] = mask("x" * 8 if c.get(enc) else "")  # never return secrets
            fields[f"{k}_set"] = bool(c.get(enc))
        st = status.get(name, {})
        age = None
        if st.get("at"):
            age = (utcnow() - datetime.fromisoformat(st["at"])).total_seconds()
        if name == "mt5" and bridge.get("last_seen") and not st and bridge.get("connector", "mt5") == "mt5":
            age = (utcnow() - datetime.fromisoformat(bridge["last_seen"])).total_seconds()
            st = {"state": "connected", "message": "MT5 bridge heartbeat"}
        out["connectors"][name] = {**meta, "configured": configured(name, cfg), "fields": fields,
                                   "status": st.get("state", "unknown"), "message": st.get("message", ""),
                                   "account": st.get("account", {}), "age_s": age}
    return out


@router.get("")
def list_brokers(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return public_view(db)


class BrokerUpdate(BaseModel):
    fields: dict
    password: str | None = None


@router.put("/{name}")
def update_broker(name: str, body: BrokerUpdate, request: Request, user: User = Depends(require("system.settings")),
                  db: Session = Depends(get_db)):
    if name not in SECRET_FIELDS:
        raise HTTPException(400, "This connector is configured on its own host")
    _confirm(user, body.password)
    cfg = state.get_locked(db, "brokers")
    c = dict(cfg.get(name, {}))
    changed = []
    for k, v in body.fields.items():
        v = (v or "").strip() if isinstance(v, str) else v
        if k in PLAIN_FIELDS[name]:
            if k == "environment":
                if v not in ("demo", "live"):
                    raise HTTPException(400, "environment must be demo or live")
                if v == "live":
                    if not user_has(user, "live.approve"):
                        raise HTTPException(403, "Missing permission: live.approve")
                    if not livegate.is_approved(db):
                        raise HTTPException(400, "Approve the live-trading gate first (Risk & Strategy page)")
            if c.get(k) != v:
                c[k] = v
                changed.append(k)
        elif k in SECRET_FIELDS[name]:
            if v:  # empty = keep the stored secret
                c[SECRET_FIELDS[name][k]] = encrypt(str(v))
                changed.append(k)
        else:
            raise HTTPException(400, f"Unknown field '{k}'")
    if not changed:
        return public_view(db)
    if cfg["active"] == name and db.scalar(
        select(func.count()).select_from(Position).where(Position.status == "OPEN", Position.mode.in_(["MT5_DEMO", "MT5_LIVE"]))
    ):
        raise HTTPException(400, "Close open broker positions before changing the active broker's credentials")
    cfg[name] = c
    cfg["version"] = int(cfg.get("version", 1)) + 1
    state.put(db, "brokers", cfg, by=user.username)
    audit(db, "broker.updated", user=user, ip=client_ip(request), entity="broker", entity_id=name,
          details={"changed": changed})  # names only, never values
    db.commit()
    return public_view(db)


class ActivateIn(BaseModel):
    name: str
    password: str


@router.post("/activate")
def activate(body: ActivateIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    if body.name not in (*CONNECTORS, "none"):
        raise HTTPException(400, "Unknown connector")
    _confirm(user, body.password)
    cfg = state.get_locked(db, "brokers")
    if body.name == cfg["active"]:
        return public_view(db)
    if body.name != "none" and not configured(body.name, cfg):
        raise HTTPException(400, "Enter and save the credentials first")
    if db.scalar(select(func.count()).select_from(Position).where(Position.status == "OPEN",
                                                                 Position.mode.in_(["MT5_DEMO", "MT5_LIVE"]))):
        raise HTTPException(400, "Close open broker positions before switching broker")
    tr = state.get(db, "trading")
    if tr["mode"] in ("MT5_DEMO", "MT5_LIVE"):
        raise HTTPException(400, "Switch trading mode to OFF, SIGNALS ONLY or PAPER before switching broker")
    prev = cfg["active"]
    cfg["active"] = body.name
    cfg["version"] = int(cfg.get("version", 1)) + 1
    state.put(db, "brokers", cfg, by=user.username)
    # Clear reconciliation state of the previous broker.
    state.put(db, "reconciliation", {"ok": True, "detail": "", "at": utcnow().isoformat()})
    state.put(db, "external_positions", {"connector": "", "at": "", "positions": []})
    audit(db, "broker.activated", user=user, ip=client_ip(request), details={"from": prev, "to": body.name})
    notify(f"Broker connection switched {prev} → {body.name} by {user.username}")
    db.commit()
    return public_view(db)


class LimitsIn(BaseModel):
    max_volume: float = Field(ge=0.001, le=100)
    max_spread_points: float = Field(ge=1, le=500)
    allow_real: bool
    password: str


@router.put("")
def update_limits(body: LimitsIn, request: Request, user: User = Depends(require("risk.edit")), db: Session = Depends(get_db)):
    _confirm(user, body.password)
    cfg = state.get_locked(db, "brokers")
    if body.allow_real and not cfg["allow_real"]:
        if not user_has(user, "live.approve"):
            raise HTTPException(403, "Missing permission: live.approve")
        if not livegate.is_approved(db):
            raise HTTPException(400, "Approve the live-trading gate first")
    changes = {k: {"from": cfg[k], "to": getattr(body, k)} for k in ("max_volume", "max_spread_points", "allow_real")
               if cfg[k] != getattr(body, k)}
    cfg.update(max_volume=body.max_volume, max_spread_points=body.max_spread_points, allow_real=body.allow_real)
    cfg["version"] = int(cfg.get("version", 1)) + 1
    state.put(db, "brokers", cfg, by=user.username)
    audit(db, "broker.limits_updated", user=user, ip=client_ip(request), details=changes)
    if changes.get("allow_real", {}).get("to"):
        notify(f"⚠️ Real-money orders ALLOWED by {user.username}")
    db.commit()
    return public_view(db)
