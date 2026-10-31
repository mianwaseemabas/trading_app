"""Crypto exchanges (Binance / OKX / Bybit): settings, keys, feed and spot orders."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import Order, Position, Quote, User, utcnow
from ..secretbox import decrypt, encrypt, mask
from ..services import trading
from ..services.crypto import desk
from ..services.crypto import exchanges as ex
from .brokers import _confirm

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


def _public(db: Session) -> dict:
    c = desk.cfg(db)
    keys = state.get(db, "crypto_keys")
    st = state.get(db, "crypto_status")
    return {
        **{k: v for k, v in c.items() if k not in ("paper_cash",)},
        "paper_cash": round(float(c["paper_cash"]), 2),
        "exchanges": [{"name": n, "label": ex.LABELS[n], "test_label": ex.TEST_LABEL[n], "has_keys": desk.has_keys(db, n),
                       "needs_passphrase": n == "okx",
                       "api_key": mask(decrypt(keys.get(n, {}).get("api_key_enc", ""))) if keys.get(n, {}).get("api_key_enc") else "",
                       "updated_at": keys.get(n, {}).get("updated_at")} for n in ex.EXCHANGES],
        "status": {k: v for k, v in st.items() if k != "balances"},
        "position_mode": desk.pos_mode(db),
    }


@router.get("/settings")
def get_settings(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    return _public(db)


class KeysIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)
    secret: str = Field(min_length=8, max_length=300)
    passphrase: str = Field(default="", max_length=100)
    password: str


@router.put("/keys/{name}")
def put_keys(name: str, body: KeysIn, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    if name not in ex.EXCHANGES:
        raise HTTPException(404, "Unknown exchange")
    _confirm(user, body.password)
    if name == "okx" and not body.passphrase:
        raise HTTPException(400, "OKX also needs the API passphrase you chose when creating the key")
    keys = state.get_locked(db, "crypto_keys")
    keys[name] = {"api_key_enc": encrypt(body.api_key.strip()), "secret_enc": encrypt(body.secret.strip()),
                  "passphrase_enc": encrypt(body.passphrase.strip()) if body.passphrase else "", "updated_at": utcnow().isoformat(),
                  "updated_by": user.username}
    state.put(db, "crypto_keys", keys, by=user.username)
    audit(db, "crypto.keys_saved", user=user, ip=client_ip(request), details={"exchange": name})
    db.commit()
    return _public(db)


@router.delete("/keys/{name}")
def delete_keys(name: str, request: Request, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    keys = state.get_locked(db, "crypto_keys")
    keys[name] = {}
    state.put(db, "crypto_keys", keys, by=user.username)
    audit(db, "crypto.keys_deleted", user=user, ip=client_ip(request), details={"exchange": name})
    db.commit()
    return _public(db)


class TestIn(BaseModel):
    exchange: str
    environment: str = "TEST"  # TEST | LIVE


@router.post("/test-connection")
def test_connection(body: TestIn, user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    if body.exchange not in ex.EXCHANGES or not desk.has_keys(db, body.exchange):
        raise HTTPException(400, "Save the API key and secret for this exchange first")
    x = ex.make(body.exchange, desk.creds(db, body.exchange), testnet=body.environment == "TEST")
    try:
        r = x.check_keys()
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}
    warn = []
    if r.get("can_withdraw"):
        warn.append("These keys CAN WITHDRAW funds. Create new keys with withdrawals disabled — the app never needs them.")
    cs = state.get(db, "crypto_status")
    cs["withdraw_enabled"] = bool(r.get("can_withdraw"))
    state.put(db, "crypto_status", cs)
    db.commit()
    return {"ok": True, "assets": r.get("assets", {}), "can_trade": r.get("can_trade", True), "warnings": warn}


class SettingsIn(BaseModel):
    mode: str | None = None
    exchange: str | None = None
    symbols: list[str] | None = None
    signal_timeframes: list[str] | None = None
    strategy: str | None = None
    allow_real: bool | None = None
    signals: bool | None = None
    fee_pct: float | None = Field(default=None, ge=0, le=1)
    max_spread_pct: float | None = Field(default=None, ge=0.01, le=2)
    paper_reset: float | None = Field(default=None, ge=10, le=1_000_000)
    password: str | None = None


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    c = state.get_locked(db, "crypto")
    changes = {}
    risky = (body.mode == "LIVE" and c["mode"] != "LIVE") or (body.allow_real and not c.get("allow_real"))
    if risky:
        _confirm(user, body.password)
    open_now = db.scalar(select(Position.id).where(Position.mode.in_(list(desk.CRYPTO_MODES)), Position.status == "OPEN").limit(1))
    if body.exchange is not None and body.exchange != c["exchange"]:
        if body.exchange not in ex.EXCHANGES:
            raise HTTPException(400, "Unknown exchange")
        if open_now:
            raise HTTPException(400, "Close open crypto positions before switching exchange")
        changes["exchange"] = (c["exchange"], body.exchange)
        c["exchange"] = body.exchange
    if body.mode is not None and body.mode != c["mode"]:
        if body.mode not in ("OFF", "PAPER", "TEST", "LIVE"):
            raise HTTPException(400, "mode must be OFF, PAPER, TEST or LIVE")
        if body.mode in ("TEST", "LIVE") and not desk.has_keys(db, c["exchange"]):
            raise HTTPException(400, f"Save {ex.LABELS[c['exchange']]} API keys first")
        if body.mode == "LIVE" and not (body.allow_real or c.get("allow_real")):
            raise HTTPException(400, "Tick 'Allow real-money crypto orders' first")
        from ..services.livegate import is_approved
        if body.mode == "LIVE" and not is_approved(db):
            raise HTTPException(400, "Real-money crypto needs the live-trading gate (Risk & Strategy) approved first")
        changes["mode"] = (c["mode"], body.mode)
        c["mode"] = body.mode
    if body.symbols is not None:
        syms = [s.strip().upper().replace("/", "").replace("-", "") for s in body.symbols if s.strip()]
        if not syms or len(syms) > 15 or not all(s.isalnum() and s.endswith(("USDT", "USDC")) for s in syms):
            raise HTTPException(400, "Use 1-15 USDT/USDC spot pairs, e.g. BTCUSDT, ETHUSDT")
        changes["symbols"] = (c["symbols"], syms)
        c["symbols"] = syms
    if body.signal_timeframes is not None:
        if not body.signal_timeframes or not set(body.signal_timeframes) <= {"M15", "H1", "H4", "D1"}:
            raise HTTPException(400, "signal timeframes must be among M15, H1, H4, D1")
        c["signal_timeframes"] = body.signal_timeframes
    if body.strategy is not None:
        from ..services.strategy import STRATEGIES
        if body.strategy and body.strategy not in STRATEGIES:
            raise HTTPException(400, f"Unknown strategy; choose one of {list(STRATEGIES)}")
        changes["strategy"] = (c.get("strategy"), body.strategy)
        c["strategy"] = body.strategy
    for k in ("allow_real", "signals", "fee_pct", "max_spread_pct"):
        v = getattr(body, k)
        if v is not None and v != c.get(k):
            changes[k] = (c.get(k), v)
            c[k] = v
    if body.paper_reset is not None:
        if db.scalar(select(Position.id).where(Position.mode == "CRYPTO_PAPER", Position.status == "OPEN").limit(1)):
            raise HTTPException(400, "Close open paper crypto positions before resetting")
        c["paper_cash"] = c["paper_start"] = body.paper_reset
        changes["paper_reset"] = body.paper_reset
    state.put(db, "crypto", c, by=user.username)
    if changes:
        audit(db, "crypto.settings", user=user, ip=client_ip(request), details={k: v for k, v in changes.items()})
    db.commit()
    return _public(db)


@router.get("/feed")
def feed(user: User = Depends(require("trades.view")), db: Session = Depends(get_db)):
    c = desk.cfg(db)
    mode = desk.pos_mode(db) or "CRYPTO_PAPER"
    acct, meta = desk.account(db, mode)
    st = state.get(db, "crypto_status")
    quotes = {s: db.get(Quote, s) for s in c["symbols"]}
    positions = desk.open_positions(db, mode)
    orders = db.scalars(select(Order).where(Order.mode.in_(list(desk.CRYPTO_MODES))).order_by(Order.id.desc()).limit(10)).all()
    return {
        "mode": c["mode"], "position_mode": mode, "exchange": c["exchange"], "account": acct.__dict__ if acct else None,
        "account_meta": {k: v for k, v in meta.items() if k != "balances"}, "balances": meta.get("balances", {}),
        "quotes": {s: {"bid": q.bid, "ask": q.ask, "age_s": (utcnow() - q.collected_at).total_seconds(),
                       "change_24h": (st.get("changes") or {}).get(s)} for s, q in quotes.items() if q},
        "positions": [{c2: getattr(p, c2) for c2 in ("id", "mode", "symbol", "side", "volume", "open_price", "current_price", "stop_loss",
                                                     "take_profit", "unrealized_pnl", "opened_at", "commission")} for p in positions],
        "orders": [{c2: getattr(o, c2) for c2 in ("client_id", "mode", "symbol", "side", "volume", "status", "fill_price", "error", "created_at")}
                   for o in orders],
        "status": {k: v for k, v in st.items() if k != "balances"},
    }


class CryptoTicket(BaseModel):
    symbol: str
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    sizing_mode: str = "auto"
    sizing_value: float | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=200)


@router.post("/preview")
def preview(body: CryptoTicket, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    if body.sizing_mode not in trading.SIZING_MODES:
        raise HTTPException(400, "unknown sizing mode")
    d, ctx, mode = desk.preview(db, body.symbol.upper(), body.stop_loss, body.take_profit, body.sizing_mode, body.sizing_value)
    out = trading.potential_outcomes(d, ctx)
    db.rollback()
    return {"mode": mode, "approved": d.approved, "volume": d.volume, "entry_price": d.entry_price, "risk_amount": d.risk_amount,
            "risk_budget": d.risk_budget, "checks": d.checks, "bid": ctx.bid, "ask": ctx.ask, "notional": round(d.volume * d.entry_price, 2),
            "max_volume": trading.floor_to_step(d.risk_budget / d.loss_per_lot, ctx.spec.volume_step) if d.loss_per_lot else None,
            "volume_min": ctx.spec.volume_min, "equity": ctx.account.equity, "cash": ctx.account.free_margin, **out}


@router.post("/order")
def order(body: CryptoTicket, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    if not state.get(db, "controls").get("manual_trading", True):
        raise HTTPException(400, "Manual trading is switched off")
    try:
        p, checks, out = desk.place(db, symbol=body.symbol.upper(), sl=body.stop_loss, tp=body.take_profit,
                                    sizing_mode=body.sizing_mode, sizing_value=body.sizing_value, by=user.username, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if p is None:
        db.commit()
        failed = "; ".join(f"{c['check']} ({c.get('detail', '')})" for c in checks if not c["passed"])
        raise HTTPException(400, f"Not placed: {failed}")
    audit(db, "order.manual", user=user, ip=client_ip(request), details={"symbol": p.symbol, "side": "BUY", "volume": p.volume, "mode": p.mode})
    db.commit()
    return {"position_id": p.id, "status": p.status, "volume": p.volume, "price": p.open_price, "close_reason": p.close_reason, **out}


@router.get("/suggest")
def suggest(symbol: str, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    from ..services import market_data as md
    from ..services.indicators import add_indicators
    symbol = symbol.upper()
    q = db.get(Quote, symbol)
    df = md.load_candles(db, symbol, "H1", limit=300)
    if q is None or len(df) < 30:
        raise HTTPException(400, "No price history yet for this coin")
    atr = float(add_indicators(df, state.get(db, "strategy"))["atr"].iloc[-1])
    from ..models import Instrument
    inst = db.get(Instrument, symbol)
    entry = q.ask
    sl, tp = entry - 1.5 * atr, entry + 3 * atr
    digits = inst.digits if inst else 2
    return {"entry": entry, "stop_loss": round(sl, digits), "take_profit": round(tp, digits), "atr": atr, "timeframe": "H1"}
