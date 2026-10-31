"""Crypto desk end-to-end against a fake Binance: paper, testnet, protection, fail-closed, approval, intel."""
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import CryptoLink, Position, Quote, Signal, SignalApproval, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.crypto import desk  # noqa: E402
from app.services.crypto import exchanges as ex  # noqa: E402

PW = "Test#Operator-Pass1"
PRICE = {"BTCUSDT": 64000.0, "ETHUSDT": 3000.0}


class FakeBinance:
    def __init__(self):
        self.calls, self.fail_oco, self.oco_state, self.sold = [], False, "EXECUTING", []

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query)) or dict(urllib.parse.parse_qsl(body.decode() if body else ""))
        self.calls.append((method, u.netloc, u.path, q))
        sym = q.get("symbol", "BTCUSDT")
        px = PRICE.get(sym, 100.0)
        if u.path == "/api/v3/exchangeInfo":
            return 200, {"symbols": [{"symbol": sym, "status": "TRADING", "baseAsset": sym[:-4], "quoteAsset": "USDT",
                                      "orderTypes": ["MARKET", "LIMIT_MAKER", "STOP_LOSS", "STOP_LOSS_LIMIT"],
                                      "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                                  {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                                                  {"filterType": "NOTIONAL", "minNotional": "5"}]}]}
        if u.path == "/api/v3/ticker/bookTicker":
            return 200, {"bidPrice": str(px), "askPrice": str(px + 0.01)}
        if u.path == "/api/v3/ticker/24hr":
            return 200, {"priceChangePercent": "1.25"}
        if u.path == "/api/v3/klines":
            n = int(q["limit"])
            step = {"1h": 3600, "15m": 900, "4h": 14400, "1d": 86400}[q["interval"]]
            now = int(datetime.now(timezone.utc).timestamp()) // step * step
            rows = []
            for k in range(n, 0, -1):
                t = now - k * step
                c = px * (1 + 0.0004 * ((k % 7) - 3))
                rows.append([t * 1000, str(c * 0.999), str(c * 1.002), str(c * 0.997), str(c), "10"])
            return 200, rows
        if u.path == "/api/v3/account":
            return 200, {"canTrade": True, "canWithdraw": False, "balances": [{"asset": "USDT", "free": "100", "locked": "0"},
                                                                             {"asset": "BTC", "free": "0.0004", "locked": "0"}]}
        if u.path == "/api/v3/order" and method == "POST":
            qty = float(q["quantity"])
            if q["side"] == "SELL":
                self.sold.append(qty)
            return 200, {"orderId": len(self.calls), "status": "FILLED", "executedQty": q["quantity"], "cummulativeQuoteQty": str(qty * px),
                         "fills": [{"price": str(px), "qty": q["quantity"], "commission": str(qty * px * 0.001), "commissionAsset": "USDT"}]}
        if u.path == "/api/v3/orderList/oco":
            if self.fail_oco:
                return 400, {"code": -1013, "msg": "Filter failure: PERCENT_PRICE_BY_SIDE"}
            return 200, {"orderListId": 501}
        if u.path == "/api/v3/orderList" and method == "GET":
            return 200, {"listOrderStatus": self.oco_state, "orders": [{"orderId": 9001}]}
        if u.path == "/api/v3/order" and method == "GET":
            return 200, {"orderId": 9001, "type": "STOP_LOSS", "status": "FILLED", "executedQty": "0.0003", "cummulativeQuoteQty": str(0.0003 * 62000)}
        if u.path == "/api/v3/orderList" and method == "DELETE":
            return 200, {}
        return 404, {"msg": f"no route {u.path}"}


@pytest.fixture(scope="module")
def fake():
    return FakeBinance()


@pytest.fixture(scope="module", autouse=True)
def fresh_db(fake):
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    c = state.get(db, "crypto")
    c.update(symbols=["BTCUSDT", "ETHUSDT"], timeframes=["H1"])
    state.put(db, "crypto", c)
    db.commit()
    db.close()
    yield


@pytest.fixture(autouse=True)
def transport(fake, monkeypatch):
    monkeypatch.setattr(ex, "default_http", fake)
    desk._mem.update(backoff_until=0, bal_at=0, candles_at={}, prot_at={}, chg_at=0)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def c():
    cl = TestClient(app)
    r = cl.post("/api/auth/login", json={"username": "op", "password": PW})
    cl.headers["x-csrf-token"] = r.json()["user"]["csrf_token"]
    return cl


def market(db):
    desk.refresh_market(db)
    db.commit()


def test_market_data_and_paper_trade(c, db):
    market(db)
    assert db.get(Quote, "BTCUSDT").bid == 64000 and state.get(db, "crypto_status")["market_ok"]
    r = c.put("/api/crypto/settings", json={"mode": "PAPER"})
    assert r.status_code == 200 and r.json()["position_mode"] == "CRYPTO_PAPER"
    sl, tp = 62720.0, 66560.0
    pv = c.post("/api/crypto/preview", json={"symbol": "BTCUSDT", "stop_loss": sl, "take_profit": tp, "sizing_mode": "auto"}).json()
    assert pv["approved"], [x for x in pv["checks"] if not x["passed"]]
    assert 0 < pv["risk_amount"] <= 0.5 and pv["notional"] < 50
    bad = c.post("/api/crypto/preview", json={"symbol": "BTCUSDT", "stop_loss": 65000, "sizing_mode": "auto"}).json()
    assert not bad["approved"]
    r = c.post("/api/crypto/order", json={"symbol": "BTCUSDT", "stop_loss": sl, "take_profit": tp, "sizing_mode": "auto"})
    assert r.status_code == 200, r.text
    pid = r.json()["position_id"]
    db.expire_all()
    cash = state.get(db, "crypto")["paper_cash"]
    assert cash < 100 - pv["notional"] * 0.99
    # price falls through the stop -> paper position closes with a loss within budget
    PRICE["BTCUSDT"] = 62700.0
    desk._mem["backoff_until"] = 0
    market(db)
    desk.monitor(db)
    db.commit()
    db.expire_all()
    p = db.get(Position, pid)
    assert p.status == "CLOSED" and p.close_reason == "stop_loss" and -0.6 < p.realized_pnl < 0
    PRICE["BTCUSDT"] = 64000.0


def test_testnet_order_with_oco_and_exit(c, db, fake):
    market(db)
    r = c.put("/api/crypto/settings", json={"mode": "TEST"})
    assert r.status_code == 400  # keys required
    r = c.put("/api/crypto/keys/binance", json={"api_key": "testkey-123456", "secret": "testsecret-123456", "password": PW})
    assert r.status_code == 200 and r.json()["exchanges"][0]["has_keys"]
    assert "testsecret" not in r.text
    assert c.post("/api/crypto/test-connection", json={"exchange": "binance"}).json()["ok"]
    assert c.put("/api/crypto/settings", json={"mode": "TEST"}).status_code == 200
    desk._mem["backoff_until"] = 0
    start = len(fake.calls)
    market(db)
    r = c.post("/api/crypto/order", json={"symbol": "BTCUSDT", "stop_loss": 62720.0, "take_profit": 66560.0})
    assert r.status_code == 200, r.text
    pid = r.json()["position_id"]
    assert {n for _, n, _, _ in fake.calls[start:]} == {"testnet.binance.vision"}
    link = db.get(CryptoLink, pid)
    assert link.prot_kind == "oco" and link.prot_ids == ["501"] and link.environment == "test"
    # exchange reports the OCO stop filled -> app closes the position
    fake.oco_state = "ALL_DONE"
    desk.monitor(db)
    db.commit()
    db.expire_all()
    p = db.get(Position, pid)
    assert p.status == "CLOSED" and p.close_reason == "stop_loss" and p.close_price == pytest.approx(62000)


def test_fail_closed_when_stop_cannot_be_placed(c, db, fake):
    fake.fail_oco = True
    fake.sold.clear()
    r = c.post("/api/crypto/order", json={"symbol": "BTCUSDT", "stop_loss": 62720.0, "take_profit": 66560.0})
    fake.fail_oco = False
    assert r.status_code == 200
    db.expire_all()
    p = db.get(Position, r.json()["position_id"])
    assert p.status == "CLOSED" and p.close_reason == "protection_failed" and fake.sold


def test_spot_cannot_short_and_close_dispatch(c, db):
    q = db.get(Quote, "ETHUSDT")
    d, ctx, mode = desk.preview(db, "ETHUSDT", q.bid + 50, None, "auto", None)
    db.rollback()
    c.put("/api/crypto/settings", json={"mode": "PAPER"})
    ctx = desk.build_context(db, "ETHUSDT", "SELL", q.bid + 50, q.bid - 100, "CRYPTO_PAPER")
    from app.services.risk_engine import evaluate
    assert any(x["check"].startswith("spot account") and not x["passed"] for x in evaluate(ctx).checks)
    db.rollback()
    r = c.post("/api/crypto/order", json={"symbol": "ETHUSDT", "stop_loss": 2940.0, "take_profit": 3120.0})
    assert r.status_code == 200, r.text
    pid = r.json()["position_id"]
    assert c.post(f"/api/positions/{pid}/close").json()["status"] == "closed"
    db.expire_all()
    assert db.get(Position, pid).close_reason == "manual"


def test_crypto_setup_approval_and_intel(c, db):
    q = db.get(Quote, "BTCUSDT")
    sig = Signal(symbol="BTCUSDT", timeframe="H1", candle_time=utcnow() - timedelta(minutes=3), strategy="smc_sweep_bos",
                 strategy_version="cx1", action="BUY", entry_ref=q.ask, stop_loss=round(q.ask * 0.98, 2), take_profit=round(q.ask * 1.04, 2),
                 features={}, reasons=[{"side": "BUY", "check": "sweep", "passed": True}], status="AWAITING", trading_mode="CRYPTO_PAPER")
    db.add(sig)
    db.flush()
    db.add(SignalApproval(signal_id=sig.id, expires_at=utcnow() + timedelta(minutes=30)))
    db.commit()
    rep = c.get(f"/api/setups/{sig.id}/report").json()
    assert rep["risk_now"]["approved"], rep["risk_now"]
    r = c.post(f"/api/setups/{sig.id}/approve", json={"note": "sweep + BOS"})
    assert r.status_code == 200, r.text
    wl = c.get("/api/intel/watchlist").json()
    btc = next(w for w in wl if w["symbol"] == "BTCUSDT")
    assert btc["asset"] == "crypto" and btc["venue"] == "binance" and btc["change_24h"] == 1.25 and btc["digits"] == 2
    t = c.get("/api/intel/terminal?symbol=BTCUSDT").json()
    assert t["venue"] == "crypto" and t["account"]["mode"] == "CRYPTO_PAPER" and t["account"]["open_positions"] == 1
    assert t["risk"]["level"] in ("Low", "Medium", "High") and t["insights"]["items"] and t["achievements"]["items"]
    assert 0 <= t["insights"]["agreement"] <= 100
    cd = c.get("/api/intel/candles?symbol=BTCUSDT&tf=H1&limit=50").json()
    assert len(cd["candles"]) == 50
    assert c.put("/api/intel/favorites", json={"symbols": ["btcusdt"]}).json() == ["BTCUSDT"]
    assert c.post("/api/intel/brief", json={"symbol": "BTCUSDT"}).status_code == 400  # AI not enabled
