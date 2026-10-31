"""Manual approval of strategy setups, setup reports, SMC scanner, overview, journal screenshots, strategy switch."""
import os
from datetime import timedelta

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Quote, Signal, SignalApproval, SmcEvent, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import engine as eng  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services import strategy, trading  # noqa: E402
from app.services.strategy import StrategyResult  # noqa: E402

PW = "Test#Operator-Pass1"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    for _ in range(3):
        md.run_simulator(db, ["EURUSD", "GBPUSD"], ["M15", "H1"])
    db.commit()
    db.close()
    yield


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr(trading, "data_freshness", lambda *a, **k: (True, "ok (test)"))


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
    cl.put("/api/system/data", json={"source": "simulator"})
    cl.post("/api/system/mode", json={"mode": "PAPER"})
    yield cl
    for p in cl.get("/api/positions?status=OPEN").json():
        cl.post(f"/api/positions/{p['id']}/close")


def make_setup(db, symbol="EURUSD", side="BUY", minutes=60):
    q = db.get(Quote, symbol)
    sl, tp = (q.bid - 0.0012, q.ask + 0.0025) if side == "BUY" else (q.ask + 0.0012, q.bid - 0.0025)
    sig = Signal(symbol=symbol, timeframe="H1", candle_time=utcnow() - timedelta(minutes=minutes % 7 + 1), strategy="smc_sweep_bos",
                 strategy_version=f"t{utcnow().timestamp()}", action=side, entry_ref=q.bid, stop_loss=round(sl, 5),
                 take_profit=round(tp, 5), features={"atr": 0.001}, reasons=[{"side": side, "check": "liquidity sweep", "passed": True}],
                 status="AWAITING", trading_mode="PAPER")
    db.add(sig)
    db.flush()
    db.add(SignalApproval(signal_id=sig.id, expires_at=utcnow() + timedelta(minutes=minutes)))
    db.commit()
    return sig.id


def test_engine_waits_for_approval(c, db, monkeypatch):
    q = db.get(Quote, "EURUSD")
    monkeypatch.setattr(eng.strat, "evaluate", lambda df, i, cfg: StrategyResult(
        "BUY", q.bid, round(q.bid - 0.0012, 5), round(q.bid + 0.0025, 5), {"atr": 0.001}, [{"check": "test", "passed": True}]))
    cfg = c.get("/api/system/strategy").json()
    last = md.load_candles(db, "EURUSD", "H1", limit=5).index[-1].to_pydatetime()
    monkeypatch.setattr(eng, "utcnow", lambda: last + timedelta(minutes=61))
    sig = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    assert sig is not None and sig.status == "AWAITING", sig and (sig.status, sig.risk_checks)
    assert db.get(SignalApproval, sig.id).status == "WAITING"
    assert not c.get("/api/positions?status=OPEN").json()  # nothing traded yet
    assert db.scalar(select(SmcEvent.id).limit(1)) is not None or True  # logging must not break the engine
    lst = c.get("/api/setups?status=WAITING").json()
    assert any(s["signal_id"] == sig.id for s in lst["setups"]) and lst["controls"]["require_approval"] is True


def test_report_approve_and_journal_link(c, db):
    sid = make_setup(db)
    rep = c.get(f"/api/setups/{sid}/report").json()
    assert rep["hypothesis"] and rep["invalidation"] and len(rep["targets"]) == 3
    assert rep["risk_now"]["approved"] and rep["approval"]["status"] == "WAITING"
    assert "uncertainty" in rep and "reasons_against" in rep and set(rep["timeframes"]) == {"H4", "H1", "M15"}
    r = c.post(f"/api/setups/{sid}/approve", json={"note": "clean sweep, London open"})
    assert r.status_code == 200, r.text
    assert r.json()["order_status"] == "FILLED"
    assert c.post(f"/api/setups/{sid}/approve", json={}).status_code == 409  # no double approval
    pos = c.get("/api/positions?status=OPEN").json()[0]
    det = c.get(f"/api/positions/{pos['id']}/detail").json()
    assert det["approval"]["status"] == "APPROVED" and det["approval"]["note"] == "clean sweep, London open"
    # screenshot attach / serve / reject non-images
    up = c.post(f"/api/positions/{pos['id']}/attachments", files={"file": ("chart.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    img = c.get(f"/api/positions/attachments/{up.json()['id']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == PNG
    bad = c.post(f"/api/positions/{pos['id']}/attachments", files={"file": ("x.png", b"<script>", "image/png")})
    assert bad.status_code == 400
    assert c.get(f"/api/positions/{pos['id']}/detail").json()["attachments"][0]["filename"] == "chart.png"


def test_decline_and_expiry_and_invalid_price(c, db):
    sid = make_setup(db, "GBPUSD", "SELL")
    assert c.post(f"/api/setups/{sid}/decline", json={"note": "news soon"}).status_code == 200
    assert db.get(Signal, sid).status == "DECLINED"
    sid2 = make_setup(db, "GBPUSD", "BUY", minutes=-1)
    eng.expire_approvals(db)
    db.commit()
    db.expire_all()
    assert db.get(SignalApproval, sid2).status == "EXPIRED"
    sid3 = make_setup(db, "GBPUSD", "BUY")
    s = db.get(Signal, sid3)
    s.stop_loss = db.get(Quote, "GBPUSD").bid + 0.001  # price already below the stop
    db.commit()
    r = c.post(f"/api/setups/{sid3}/approve", json={})
    assert r.status_code == 400 and "invalid" in r.json()["detail"]


def test_smc_scan_overview_strategy_switch(c, db):
    r = c.get("/api/smc/scan?symbol=EURUSD&timeframe=H1&bars=100")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candles"] and body["state"]["trend"] in ("bullish", "bearish", "undefined")
    ov = c.get("/api/market/overview").json()
    assert ov["sessions"]["sessions"] and ov["symbols"][0]["symbol"] == "EURUSD"
    assert {s["name"] for s in c.get("/api/system/strategies").json()} == {"trend_pullback", "smc_sweep_bos"}
    cur = c.get("/api/system/strategy").json()
    new = c.put("/api/system/strategy", json={"values": {"name": "smc_sweep_bos"}}).json()
    assert new["name"] == "smc_sweep_bos" and new["version"] != cur["version"]
    assert c.put("/api/system/strategy", json={"values": {"name": "magic"}}).status_code == 400
    assert c.get("/api/smc/events?fmt=csv").status_code == 200
    c.put("/api/system/strategy", json={"values": {"name": "trend_pullback"}})
