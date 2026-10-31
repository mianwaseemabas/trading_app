"""API used by the MT5 bridge running on the Windows execution host.

Every request is authenticated with HMAC-SHA256 over
    timestamp \n nonce \n METHOD \n path \n sha256(body)
using the shared BRIDGE_SECRET, with clock-skew and single-use nonce checks
(replay protection). Optionally restricted to BRIDGE_ALLOWED_IPS. Transport
encryption is provided by HTTPS.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit, event
from ..config import settings
from ..db import get_db
from ..deps import client_ip
from ..models import AccountSnapshot, BridgeNonce, Instrument, Order, Position, Quote, utcnow
from ..services import livegate
from ..services import market_data as md
from ..services.notifier import notify
from ..secretbox import decrypt, encrypt
from ..services.trading import order_payload
import ipaddress

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}


async def bridge_auth(request: Request, db: Session = Depends(get_db)) -> dict:
    if not settings.bridge_secret:
        raise HTTPException(503, "Bridge is not configured")
    ip = client_ip(request)
    if settings.bridge_allowed_ips and ip not in settings.bridge_allowed_ips:
        raise HTTPException(403, "IP not allowed")
    key_id = request.headers.get("x-bridge-key-id", "")
    ts = request.headers.get("x-timestamp", "")
    nonce = request.headers.get("x-nonce", "")
    sig = request.headers.get("x-signature", "")
    if key_id != settings.bridge_key_id or not ts.isdigit() or not (16 <= len(nonce) <= 64) or not sig:
        raise HTTPException(401, "Bad bridge credentials")
    if abs(time.time() - int(ts)) > settings.bridge_max_clock_skew_s:
        raise HTTPException(401, "Timestamp outside allowed skew; check the Windows clock")
    body = await request.body()
    msg = "\n".join([ts, nonce, request.method.upper(), request.url.path, hashlib.sha256(body).hexdigest()])
    expected = hmac.new(settings.bridge_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "Bad signature")
    db.add(BridgeNonce(nonce=nonce))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(401, "Replayed nonce")
    connector = (request.headers.get("x-connector") or "mt5").lower()
    path = request.url.path
    if path in INTERNAL_PATHS:
        _internal_only(request)
    elif connector != state.get(db, "brokers")["active"]:
        # Only the broker selected in the dashboard may feed data or receive orders.
        raise HTTPException(409, f"Connector '{connector}' is not the active broker")
    if path not in INTERNAL_PATHS:
        b = state.get(db, "bridge")
        b.update(last_seen=utcnow().isoformat(), ip=ip, connector=connector)
        state.put(db, "bridge", b)
        db.commit()
    return {"ip": ip, "connector": connector}


INTERNAL_PATHS = {"/api/bridge/config", "/api/bridge/status", "/api/bridge/seed"}


def _internal_only(request: Request) -> None:
    """Credentials are only served to connectors on the server's private Docker network."""
    if request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for"):
        raise HTTPException(403, "Not available through the public proxy")
    host = request.client.host if request.client else ""
    if host == "testclient" and settings.is_dev:
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise HTTPException(403, "Internal endpoint")
    if not (ip.is_private or ip.is_loopback):
        raise HTTPException(403, "Internal endpoint")


def _set_status(db: Session, connector: str, st: str, message: str = "", account: dict | None = None) -> None:
    cs = state.get(db, "connector_status")
    cur = cs.get(connector, {})
    cur.update(state=st, message=message[:500], at=utcnow().isoformat())
    if account is not None:
        cur["account"] = account
    cs[connector] = cur
    state.put(db, "connector_status", cs)


@router.get("/config")
def connector_config(connector: str, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    cfg = state.get(db, "brokers")
    gate = state.get(db, "live_gate")
    c = cfg.get(connector, {})
    out = {"active": cfg["active"] == connector, "version": cfg.get("version", 1),
           "allow_real": bool(cfg.get("allow_real") and livegate.is_approved(db)),
           "max_volume": cfg.get("max_volume", 0.10), "max_spread_points": cfg.get("max_spread_points", 30)}
    for k, v in c.items():
        if k.endswith("_enc"):
            out[k[:-4]] = decrypt(v)
        else:
            out[k] = v
    return out


@router.post("/status")
async def connector_status(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    body = await request.json()
    _set_status(db, auth["connector"], str(body.get("state", "unknown"))[:20], str(body.get("message", "")))
    db.commit()
    return {"status": "ok"}


@router.post("/seed")
async def seed(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    """First-run migration: credentials that were set in .env are copied into the dashboard settings."""
    body = await request.json()
    name = auth["connector"]
    cfg = state.get_locked(db, "brokers")
    if name not in ("capital", "ctrader"):
        raise HTTPException(400, "Unknown connector")
    from .brokers import configured
    if configured(name, cfg):
        return {"status": "already_configured"}
    c = dict(cfg.get(name, {}))
    for k, v in (body.get("fields") or {}).items():
        if v in (None, ""):
            continue
        if k in ("api_key", "api_password", "client_secret", "access_token", "refresh_token"):
            c[f"{k}_enc"] = encrypt(str(v))
        elif k in ("identifier", "account_id", "environment", "client_id", "account_login"):
            c[k] = str(v)
    cfg[name] = c
    if cfg["active"] == "none":
        cfg["active"] = name
    cfg["version"] = int(cfg.get("version", 1)) + 1
    state.put(db, "brokers", cfg)
    audit(db, "broker.seeded_from_env", details={"connector": name, "active": cfg["active"]})
    db.commit()
    return {"status": "seeded", "active": cfg["active"] == name}


def _ts(v) -> datetime:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return md.as_utc(datetime.fromisoformat(str(v).replace("Z", "+00:00")))


@router.post("/heartbeat")
async def heartbeat(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    body = await request.json()
    acc = body.get("account") or {}
    tm = acc.get("trade_mode")
    trade_mode = TRADE_MODES.get(tm, str(tm)) if isinstance(tm, int) else str(tm or "")
    db.add(AccountSnapshot(
        source="mt5", login=str(acc.get("login", "")), server=str(acc.get("server", ""))[:64],
        trade_mode=trade_mode, currency=str(acc.get("currency", "USD"))[:8],
        balance=float(acc.get("balance", 0)), equity=float(acc.get("equity", 0)),
        margin=float(acc.get("margin", 0)), free_margin=float(acc.get("margin_free", acc.get("free_margin", 0))),
        leverage=int(acc.get("leverage", 0) or 0),
    ))
    b = state.get(db, "bridge")
    b.update(terminal=body.get("terminal") or {}, version=str(body.get("version", "")))
    state.put(db, "bridge", b)
    _set_status(db, auth["connector"], "connected", "", {
        "login": str(acc.get("login", "")), "server": str(acc.get("server", "")), "trade_mode": trade_mode,
        "currency": acc.get("currency"), "balance": acc.get("balance"), "equity": acc.get("equity")})
    db.commit()
    tr = state.get(db, "trading")
    return {"status": "ok", "mode": tr["mode"], "kill_switch": tr["kill_switch"],
            "symbols": state.get(db, "strategy")["symbols"], "timeframes": state.get(db, "strategy")["timeframes"]}


@router.post("/symbols")
async def symbols(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    for s in (await request.json()).get("symbols", []):
        inst = md.ensure_instrument(db, s["symbol"])
        for k_src, k_dst in [("digits", "digits"), ("point", "point"), ("trade_contract_size", "contract_size"),
                             ("trade_tick_size", "tick_size"), ("trade_tick_value", "tick_value"),
                             ("volume_min", "volume_min"), ("volume_max", "volume_max"), ("volume_step", "volume_step"),
                             ("currency_base", "currency_base"), ("currency_profit", "currency_profit")]:
            if s.get(k_src) is not None:
                setattr(inst, k_dst, s[k_src])
        inst.trade_allowed = bool(s.get("trade_allowed", True))
        inst.source = "mt5"
    db.commit()
    return {"status": "ok"}


@router.post("/quotes")
async def quotes(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    if state.get(db, "data")["source"] != "mt5":
        return {"status": "ignored", "reason": "data source is not mt5"}
    now = utcnow()
    for q in (await request.json()).get("quotes", []):
        bid, ask = float(q["bid"]), float(q["ask"])
        if bid <= 0 or ask < bid:
            event(db, "data", f"Invalid quote for {q.get('symbol')}: bid {bid} ask {ask}", "WARNING")
            continue
        row = db.get(Quote, q["symbol"])
        if row is None:
            row = Quote(symbol=q["symbol"], bid=bid, ask=ask, time=now)
            db.add(row)
        row.bid, row.ask, row.time, row.collected_at, row.source = bid, ask, _ts(q.get("time", now.timestamp())), now, "mt5"
    db.commit()
    return {"status": "ok"}


@router.post("/candles")
async def candles(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    if state.get(db, "data")["source"] != "mt5":
        return {"status": "ignored", "reason": "data source is not mt5"}
    body = await request.json()
    symbol, tf = body["symbol"], body["timeframe"]
    if tf not in md.TF_SECONDS:
        raise HTTPException(400, "Unsupported timeframe")
    md.ensure_instrument(db, symbol)
    rows = [dict(c, time=_ts(c["time"])) for c in body.get("candles", [])]
    n, errors = md.upsert_candles(db, symbol, tf, rows, "mt5")
    if errors:
        event(db, "data", f"{len(errors)} invalid {symbol} {tf} candles rejected", "WARNING", {"errors": errors[:20]})
    db.commit()
    return {"status": "ok", "stored": n, "rejected": len(errors)}


@router.get("/orders/pending")
def pending_orders(auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    tr = state.get(db, "trading")
    now = utcnow()
    orders = db.scalars(
        select(Order).where(Order.status == "PENDING", Order.mode.in_(["MT5_DEMO", "MT5_LIVE"])).order_by(Order.id)
    ).all()
    out = []
    for o in orders:
        if o.expires_at and o.expires_at < now:
            continue
        if o.action == "OPEN" and (tr["kill_switch"] or tr["locked"] or tr["mode"] != o.mode):
            o.status, o.error = "CANCELLED", "blocked at dispatch (kill switch, lock or mode change)"
            continue
        o.status = "SENT"
        out.append({**order_payload(o), "signature": o.signature})
    db.commit()
    return {"orders": out, "kill_switch": tr["kill_switch"]}


@router.post("/orders/{client_id}/result")
async def order_result(client_id: str, request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    body = await request.json()
    o = db.scalar(select(Order).where(Order.client_id == client_id))
    if o is None:
        raise HTTPException(404, "Unknown order")
    status = body.get("status")
    if status not in {"FILLED", "REJECTED"}:
        raise HTTPException(400, "status must be FILLED or REJECTED")
    o.status = status
    o.error = str(body.get("error", ""))[:2000]
    if status == "FILLED":
        o.broker_ticket = str(body.get("ticket", ""))
        o.fill_price = float(body.get("price", 0))
        o.filled_volume = float(body.get("volume", o.volume))
        inst = db.get(Instrument, o.symbol)
        if inst and o.requested_price:
            o.slippage_points = round(abs(o.fill_price - o.requested_price) / inst.point, 1)
        o.sl_verified = bool(body.get("sl_verified", False))
        if o.action == "MODIFY":
            p = db.get(Position, o.position_id) if o.position_id else None
            if p is not None:
                old = (p.stop_loss, p.take_profit)
                p.stop_loss = body.get("sl", o.stop_loss)
                p.take_profit = body.get("tp", o.take_profit)
                audit(db, "position.sl_tp_modified", entity="position", entity_id=p.id,
                      details={"from": old, "to": (p.stop_loss, p.take_profit), "mode": o.mode})
        if o.action == "OPEN":
            db.add(Position(
                mode=o.mode, order_id=o.id, broker_ticket=str(body.get("position_ticket") or o.broker_ticket),
                symbol=o.symbol, side=o.side, volume=o.filled_volume, open_price=o.fill_price,
                stop_loss=body.get("sl", o.stop_loss), take_profit=body.get("tp", o.take_profit),
                current_price=o.fill_price,
            ))
            db.flush()
            new_pos = db.scalar(select(Position).where(Position.order_id == o.id))
            if new_pos is not None:
                from ..services.trading import attach_management, management_for_order
                attach_management(db, new_pos, management_for_order(db, o))
            if not o.sl_verified:
                tr = state.get_locked(db, "trading")
                tr.update(locked=True, lock_reason=f"Stop-loss not verified at broker for {client_id}")
                state.put(db, "trading", tr)
                event(db, "execution", f"Stop-loss NOT verified for {client_id}; trading locked", "ERROR")
                from ..services.alerts import raise_alert
                raise_alert(db, "PROTECTION_FAILURE", f"Stop-loss NOT verified at broker for {o.symbol}",
                            f"Order {client_id}. Trading is locked until you review it.", severity="critical", dedup=f"slv:{client_id}")
    audit(db, f"order.{status.lower()}", entity="order", entity_id=client_id,
          details={k: body.get(k) for k in ("ticket", "price", "volume", "retcode", "error", "sl_verified")})
    notify(f"{'✅' if status == 'FILLED' else '❌'} {o.mode} {o.action} {o.side} {o.volume} {o.symbol}: {status} "
           f"{body.get('error', '')}", state.get(db, "notifications")["notify_orders"])
    db.commit()
    return {"status": "ok"}


@router.post("/positions")
async def positions(request: Request, auth=Depends(bridge_auth), db: Session = Depends(get_db)):
    """Full snapshot of the bridge's positions (our magic number) + recently closed deals.

    Reconciles local records with the broker; any unexplained difference blocks new trades.
    """
    body = await request.json()
    state.put(db, "external_positions", {"connector": auth["connector"], "at": utcnow().isoformat(),
                                         "positions": (body.get("external") or [])[:100]})
    acc_mode = state.get(db, "trading")["mode"]
    mode = acc_mode if acc_mode in ("MT5_DEMO", "MT5_LIVE") else body.get("mode_hint", "MT5_DEMO")
    broker = {str(p["ticket"]): p for p in body.get("positions", [])}
    local = {p.broker_ticket: p for p in db.scalars(
        select(Position).where(Position.mode.in_(["MT5_DEMO", "MT5_LIVE"]), Position.status == "OPEN")).all()}
    problems = []

    for ticket, bp in broker.items():
        lp = local.get(ticket)
        if lp is None:
            problems.append(f"broker position {ticket} {bp.get('symbol')} not in local records")
            db.add(Position(mode=mode, broker_ticket=ticket, symbol=bp["symbol"], side=bp["side"],
                            volume=float(bp["volume"]), open_price=float(bp["price_open"]),
                            stop_loss=bp.get("sl") or None, take_profit=bp.get("tp") or None,
                            opened_at=_ts(bp.get("time", utcnow().timestamp())),
                            unrealized_pnl=float(bp.get("profit", 0)), current_price=bp.get("price_current"),
                            swap=float(bp.get("swap", 0))))
            continue
        lp.unrealized_pnl = float(bp.get("profit", 0)) + float(bp.get("swap", 0))
        lp.current_price = bp.get("price_current")
        lp.stop_loss = bp.get("sl") or None
        lp.take_profit = bp.get("tp") or None
        lp.swap = float(bp.get("swap", 0))
        if abs(float(bp["volume"]) - lp.volume) > 1e-9:
            problems.append(f"volume mismatch on {ticket}: broker {bp['volume']} local {lp.volume}")
            lp.volume = float(bp["volume"])
        if not lp.stop_loss:
            problems.append(f"position {ticket} has no stop-loss at broker")

    closed = {str(d["position_ticket"]): d for d in body.get("closed", [])}
    for ticket, lp in local.items():
        if ticket in broker:
            continue
        d = closed.get(ticket)
        lp.status, lp.closed_at = "CLOSED", utcnow()
        if d:
            lp.close_price = float(d.get("price", 0))
            lp.commission = float(d.get("commission", 0))
            lp.swap = float(d.get("swap", 0))
            lp.realized_pnl = round(float(d.get("profit", 0)) + lp.commission + lp.swap, 2)
            lp.close_reason = str(d.get("reason", "closed_at_broker"))
            lp.closed_at = _ts(d.get("time", utcnow().timestamp()))
        else:
            lp.close_reason = "closed_at_broker (deal not yet reported)"
        lp.unrealized_pnl = 0
        audit(db, "position.closed", entity="position", entity_id=lp.id,
              details={"mode": lp.mode, "symbol": lp.symbol, "pnl": lp.realized_pnl, "reason": lp.close_reason})
        notify(f"Position {lp.symbol} {lp.side} closed at broker ({lp.close_reason}) P&L {lp.realized_pnl}")

    # Late deal reports for positions already marked closed.
    for ticket, d in closed.items():
        lp = db.scalar(select(Position).where(Position.broker_ticket == ticket, Position.status == "CLOSED",
                                              Position.realized_pnl.is_(None)))
        if lp:
            lp.close_price = float(d.get("price", 0))
            lp.commission, lp.swap = float(d.get("commission", 0)), float(d.get("swap", 0))
            lp.realized_pnl = round(float(d.get("profit", 0)) + lp.commission + lp.swap, 2)
            lp.close_reason = str(d.get("reason", "closed_at_broker"))

    stuck = db.scalars(select(Order).where(Order.status == "SENT", Order.updated_at < utcnow() - timedelta(minutes=2))).all()
    if stuck:
        problems.append(f"{len(stuck)} order(s) sent to bridge without a result: " + ", ".join(o.client_id for o in stuck[:5]))

    rec = {"ok": not problems, "detail": "; ".join(problems)[:1000], "at": utcnow().isoformat()}
    prev = state.get(db, "reconciliation")
    state.put(db, "reconciliation", rec)
    if problems and prev.get("detail") != rec["detail"]:
        event(db, "reconciliation", "Reconciliation mismatch: " + rec["detail"], "ERROR")
        notify("⚠️ Reconciliation mismatch: " + rec["detail"])
    db.commit()
    return {"status": "ok", "reconciliation": rec}
