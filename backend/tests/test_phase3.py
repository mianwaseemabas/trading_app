"""Chart line alerts, indicators/markers in candles, strategy race, size calculator."""
import os
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Alert, Candle, Quote, RaceTrade, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import drawings, race  # noqa: E402
from app.services import market_data as md  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    md.run_simulator(db, ["EURUSD"], ["H1"])
    # 600 synthetic closed H1 candles ending now
    rng = np.random.default_rng(5)
    n = 600
    close = 1.10 + np.cumsum(rng.normal(0, 0.0012, n)) + 0.02 * np.sin(np.arange(n) / 60)
    end = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    idx = pd.date_range(end=end, periods=n, freq="h", tz="UTC")
    opn = np.r_[close[0], close[:-1]]
    for t, o, c_ in zip(idx, opn, close):
        db.merge(Candle(symbol="EURUSD", timeframe="H1", time=t.to_pydatetime(), open=float(o), high=float(max(o, c_) + 0.0006),
                        low=float(min(o, c_) - 0.0006), close=float(c_), tick_volume=100, spread_points=0, source="test", collected_at=utcnow()))
    db.commit()
    db.close()
    yield


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


def test_candles_have_indicators(c):
    r = c.get("/api/intel/candles?symbol=EURUSD&tf=H1&limit=200").json()
    last = r["candles"][-1]
    assert r["source"] == "stored" and len(r["candles"]) == 200
    assert last["bb_up"] > last["bb_lo"] and 0 <= last["rsi"] <= 100 and last["macd_hist"] is not None and last["volume"] == 100
    assert "markers" in r


def test_line_alert_fires_once(c, db):
    q = db.get(Quote, "EURUSD")
    mid = (q.bid + q.ask) / 2
    r = c.post("/api/drawings", json={"symbol": "EURUSD", "kind": "hline", "data": {"price": mid + 0.002}, "alert": True, "label": "resistance"})
    assert r.status_code == 200
    did = r.json()["id"]
    assert c.post("/api/drawings", json={"symbol": "EURUSD", "kind": "trend", "data": {"t1": 1, "p1": 1, "t2": 1, "p2": 2}}).status_code == 400
    drawings.check(db)
    db.commit()  # remembers "below"
    q.bid, q.ask = mid + 0.0025, mid + 0.0026
    db.commit()
    assert drawings.check(db) == 1
    db.commit()
    a = db.scalars(select(Alert).where(Alert.type == "PRICE_ALERT")).all()
    assert len(a) == 1 and "crossed above" in a[0].title and "resistance" in a[0].title
    assert drawings.check(db) == 0  # one-time
    lst = c.get("/api/drawings?symbol=EURUSD").json()
    assert lst[0]["alert"] is False and lst[0]["triggered_at"]
    now = time.time()
    tr = c.post("/api/drawings", json={"symbol": "EURUSD", "kind": "trend", "data": {"t1": now - 3600, "p1": 1.0, "t2": now, "p2": 1.1}}).json()
    assert abs(tr["level_now"] - 1.1) < 0.001
    assert c.delete(f"/api/drawings/{did}").status_code == 200


def test_race_opens_and_resolves(c, db, monkeypatch):
    from app.services import strategy as st

    def fake_eval(df, i, cfg):
        px = float(df["close"].iloc[i])
        return st.StrategyResult("BUY", px, px - 0.002, px + 0.004)
    monkeypatch.setattr(st, "evaluate", fake_eval)
    n = race.open_new(db)
    db.commit()
    assert race.open_new(db) == 0  # same candle is not processed twice
    trades = db.scalars(select(RaceTrade)).all()
    assert n == len(trades)
    assert n == len(st.STRATEGIES)
    # resolve against future candles: push a big candle that hits every stop or target
    last = md.load_candles(db, "EURUSD", "H1", limit=1).index[-1].to_pydatetime()
    db.merge(Candle(symbol="EURUSD", timeframe="H1", time=last + timedelta(hours=1), open=1.1, high=5.0, low=0.01, close=1.1,
                    tick_volume=1, spread_points=0, source="test", collected_at=utcnow()))
    db.commit()
    race.resolve(db)
    db.commit()
    for t in db.scalars(select(RaceTrade)).all():
        assert t.status == "LOSS" and t.r == pytest.approx(-1 - t.cost_r, abs=1e-3)  # both touched -> stop assumed first
    lb = c.get("/api/race").json()
    assert len(lb["board"]) >= 6 and "curve" in lb["board"][0]
    assert c.post("/api/race/reset", json={"timeframes": ["H4"]}).json()["config"]["timeframes"] == ["H4"]
    assert db.query(RaceTrade).count() == 0


def test_size_calculator(c):
    r = c.post("/api/tools/size", json={"symbol": "EURUSD", "entry": 1.1, "stop": 1.098, "target": 1.104, "risk_amount": 10}).json()
    assert r["stop_pips"] == 20.0 and r["size"] == pytest.approx(0.05) and r["rr"] == 2.0 and r["break_even_win_rate_pct"] == 33.3
    w = c.post("/api/tools/size", json={"symbol": "EURUSD", "entry": 1.1, "stop": 1.05, "risk_amount": 1}).json()
    assert "warning" in w
