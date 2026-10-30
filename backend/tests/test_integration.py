"""Integration tests against a real PostgreSQL (DATABASE_URL). Destroys data in that DB!

Run only against a throwaway test database:
    APP_ENV=test DATABASE_URL=postgresql+psycopg://.../trading_test pytest
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import timedelta

import pytest

if os.getenv("APP_ENV") != "test" and os.getenv("ALLOW_DB_TESTS") != "1":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Order, Position, Quote, Signal, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services import trading  # noqa: E402
from app.services.risk_engine import evaluate  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def login():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "op", "password": PW})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["user"]["csrf_token"]
    return c


def test_paper_trade_lifecycle(db):
    c = login()
    assert c.put("/api/system/data", json={"source": "simulator"}).status_code == 200
    assert c.post("/api/system/mode", json={"mode": "PAPER"}).status_code == 200
    md.run_simulator(db, ["EURUSD"], ["M15", "H1"])
    db.commit()
    q = db.get(Quote, "EURUSD")
    last = db.scalar(select(md.Candle).where(md.Candle.symbol == "EURUSD", md.Candle.timeframe == "H1")
                     .order_by(md.Candle.time.desc()).limit(1))
    sig = Signal(symbol="EURUSD", timeframe="H1", candle_time=last.time, strategy="test", strategy_version="t",
                 action="BUY", entry_ref=q.ask, stop_loss=round(q.bid - 0.0020, 5), take_profit=round(q.ask + 0.0040, 5))
    db.add(sig)
    db.flush()
    ctx = trading.build_context(db, symbol="EURUSD", side="BUY", tf="H1", sl=sig.stop_loss, tp=sig.take_profit, mode="PAPER")
    d = evaluate(ctx)
    assert d.approved, [x for x in d.checks if not x["passed"]]
    o = trading.place_order(db, sig, d, "PAPER")
    db.commit()
    assert o.status == "FILLED"
    pos = db.scalar(select(Position).where(Position.order_id == o.id))
    assert pos.status == "OPEN"

    # Second order is blocked by max concurrent positions.
    ctx2 = trading.build_context(db, symbol="EURUSD", side="BUY", tf="H1", sl=sig.stop_loss, tp=sig.take_profit, mode="PAPER")
    assert not evaluate(ctx2).approved

    # Price falls through the stop.
    q.bid, q.ask = sig.stop_loss - 0.0001, sig.stop_loss + 0.0000
    q.collected_at = utcnow()
    db.commit()
    trading.update_paper_positions(db)
    db.commit()
    db.refresh(pos)
    assert pos.status == "CLOSED" and pos.close_reason == "stop_loss"
    assert pos.realized_pnl < 0
    assert abs(pos.realized_pnl) <= d.risk_amount + 0.5
    bal = state.get(db, "paper_account")["balance"]
    assert bal == pytest.approx(500 + pos.realized_pnl, abs=0.01)


def test_kill_switch_blocks(db):
    c = login()
    assert c.post("/api/system/kill-switch", json={"engaged": True, "reason": "test"}).status_code == 200
    q = db.get(Quote, "EURUSD")
    ctx = trading.build_context(db, symbol="EURUSD", side="BUY", tf="H1", sl=q.bid - 0.002, tp=None, mode="PAPER")
    assert not evaluate(ctx).approved
    assert c.post("/api/system/kill-switch", json={"engaged": False}).status_code == 200


def _signed(method, path, body=b""):
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    msg = "\n".join([ts, nonce, method, path, hashlib.sha256(body).hexdigest()])
    sig = hmac.new(settings.bridge_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"x-bridge-key-id": settings.bridge_key_id, "x-timestamp": ts, "x-nonce": nonce, "x-signature": sig,
            "content-type": "application/json"}


def bpost(c, path, payload):
    body = json.dumps(payload).encode()
    return c.post(path, content=body, headers=_signed("POST", path, body))


def test_bridge_auth_and_mt5_flow(db):
    c = login()
    b = TestClient(app)
    c.post("/api/system/mode", json={"mode": "OFF"})
    assert c.put("/api/system/data", json={"source": "mt5"}).status_code == 200

    # Bad signature and replay are rejected.
    h = _signed("POST", "/api/bridge/heartbeat", b"{}")
    h["x-signature"] = "0" * 64
    assert b.post("/api/bridge/heartbeat", content=b"{}", headers=h).status_code == 401
    body = json.dumps({"account": {"trade_mode": 0, "balance": 500, "equity": 500, "margin_free": 500,
                                   "leverage": 100, "currency": "USD", "login": 1}}).encode()
    h = _signed("POST", "/api/bridge/heartbeat", body)
    assert b.post("/api/bridge/heartbeat", content=body, headers=h).status_code == 200
    assert b.post("/api/bridge/heartbeat", content=body, headers=h).status_code == 401  # replay

    assert bpost(b, "/api/bridge/symbols", {"symbols": [{"symbol": "EURUSD", "digits": 5, "point": 0.00001,
        "trade_contract_size": 100000, "trade_tick_size": 0.00001, "trade_tick_value": 1.0, "volume_min": 0.01,
        "volume_max": 100, "volume_step": 0.01, "currency_base": "EUR", "currency_profit": "USD"}]}).status_code == 200
    now = int(time.time())
    hour = now - now % 3600
    candles = [{"time": hour - 3600 * k, "open": 1.08, "high": 1.081, "low": 1.079, "close": 1.0805} for k in range(1, 5)]
    candles.append({"time": hour - 7200, "open": 1.08, "high": 1.07, "low": 1.09, "close": 1.08})  # invalid
    r = bpost(b, "/api/bridge/candles", {"symbol": "EURUSD", "timeframe": "H1", "candles": candles})
    assert r.json()["rejected"] == 1
    assert bpost(b, "/api/bridge/quotes", {"quotes": [{"symbol": "EURUSD", "bid": 1.08000, "ask": 1.08008, "time": now}]}).status_code == 200

    # MT5 demo mode needs password confirmation.
    assert c.post("/api/system/mode", json={"mode": "MT5_DEMO"}).status_code == 403
    assert c.post("/api/system/mode", json={"mode": "MT5_DEMO", "password": PW}).status_code == 200
    # Live mode is impossible without the gate.
    assert c.post("/api/system/mode", json={"mode": "MT5_LIVE", "password": PW, "confirm": "ENABLE LIVE TRADING"}).status_code == 400

    db.expire_all()
    sig = Signal(symbol="EURUSD", timeframe="H1", candle_time=md.as_utc(md.datetime.fromtimestamp(hour - 3600, md.timezone.utc)),
                 strategy="test", strategy_version="t2", action="BUY", entry_ref=1.08008, stop_loss=1.07800, take_profit=1.08400)
    db.add(sig)
    db.flush()
    d = evaluate(trading.build_context(db, symbol="EURUSD", side="BUY", tf="H1", sl=1.078, tp=1.084, mode="MT5_DEMO"))
    assert d.approved, [x for x in d.checks if not x["passed"]]
    o = trading.place_order(db, sig, d, "MT5_DEMO")
    db.commit()
    assert o.status == "PENDING" and o.signature

    r = b.get("/api/bridge/orders/pending", headers=_signed("GET", "/api/bridge/orders/pending"))
    orders = r.json()["orders"]
    assert len(orders) == 1
    payload = {k: v for k, v in orders[0].items() if k != "signature"}
    assert trading.sign_payload(payload) == orders[0]["signature"]

    r = bpost(b, f"/api/bridge/orders/{o.client_id}/result", {"status": "FILLED", "ticket": 555, "position_ticket": 555,
              "price": 1.08010, "volume": d.volume, "sl_verified": True, "sl": 1.078, "tp": 1.084})
    assert r.status_code == 200
    r = bpost(b, "/api/bridge/positions", {"positions": [{"ticket": 555, "symbol": "EURUSD", "side": "BUY",
              "volume": d.volume, "price_open": 1.0801, "sl": 1.078, "tp": 1.084, "profit": -0.4, "swap": 0}], "closed": []})
    assert r.json()["reconciliation"]["ok"] is True
    # Broker shows an unknown position -> reconciliation fails -> new trades blocked.
    r = bpost(b, "/api/bridge/positions", {"positions": [{"ticket": 555, "symbol": "EURUSD", "side": "BUY",
              "volume": d.volume, "price_open": 1.0801, "sl": 1.078, "tp": 1.084, "profit": -0.4},
              {"ticket": 777, "symbol": "EURUSD", "side": "SELL", "volume": 0.01, "price_open": 1.08, "sl": 0}], "closed": []})
    assert r.json()["reconciliation"]["ok"] is False
    db.expire_all()
    assert not trading.build_context(db, symbol="EURUSD", side="BUY", tf="H1", sl=1.078, tp=1.084, mode="MT5_DEMO").reconciliation_ok
    # Position closed at broker with deal info.
    r = bpost(b, "/api/bridge/positions", {"positions": [], "closed": [
        {"position_ticket": 555, "price": 1.078, "profit": -1.9, "commission": -0.35, "swap": 0, "reason": "stop_loss", "time": now},
        {"position_ticket": 777, "price": 1.08, "profit": 0, "commission": 0, "swap": 0, "reason": "manual", "time": now}]})
    db.expire_all()
    p = db.scalar(select(Position).where(Position.broker_ticket == "555"))
    assert p.status == "CLOSED" and p.realized_pnl == pytest.approx(-2.25)
    assert db.scalar(select(Order).where(Order.client_id == o.client_id)).status == "FILLED"
