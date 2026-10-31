"""Opportunity ranking: scan, decisions, ranking order, queue to approval / auto, AI analyst."""
import os
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
from app.models import Candle, Signal, SignalApproval, User, utcnow  # noqa: E402
from app.secretbox import encrypt  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import opportunities as op  # noqa: E402
from app.services import quality, trading  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services import strategy as st  # noqa: E402

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
    rng = np.random.default_rng(7)
    n = 500
    close = 1.08 + np.cumsum(rng.normal(0, 0.0008, n))
    end = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    idx = pd.date_range(end=end, periods=n, freq="h", tz="UTC")
    opn = np.r_[close[0], close[:-1]]
    for t, o, c_ in zip(idx, opn, close):
        db.merge(Candle(symbol="EURUSD", timeframe="H1", time=t.to_pydatetime(), open=float(o), high=float(max(o, c_) + 0.0003),
                        low=float(min(o, c_) - 0.0003), close=float(c_), tick_volume=10, spread_points=0, source="test", collected_at=utcnow()))
    q = md.latest_quotes(db) if hasattr(md, "latest_quotes") else None  # noqa: F841
    from app.models import Quote
    db.flush()
    qq = db.get(Quote, "EURUSD") or Quote(symbol="EURUSD", bid=0, ask=0, time=utcnow())
    db.add(qq)
    qq.bid, qq.ask, qq.time = float(close[-1]), float(close[-1]) + 0.00008, utcnow()
    state.put(db, "news_status", {"ok": True, "last_fetch": utcnow().isoformat(), "error": "", "count": 0})
    tr = state.get(db, "trading")
    tr["mode"] = "PAPER"
    state.put(db, "trading", tr)
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


@pytest.fixture(autouse=True)
def fake_strategy(monkeypatch):
    """Every strategy 'fires' a BUY on the last candle so the scanner has setups to rank."""
    def ev(df, i, cfg):
        if i != len(df) - 1:
            return st.StrategyResult("NONE")
        px = float(df["close"].iloc[i])
        k = {"trend_pullback": 1.0, "donchian_breakout": 1.5}.get(cfg["name"], 2.0)
        return st.StrategyResult("SELL", px, px + 0.0020 * k, px - 0.0040 * k, {}, [])
    monkeypatch.setattr(st, "evaluate", ev)
    monkeypatch.setattr(quality, "session_state", lambda *a, **k: "OPEN")
    monkeypatch.setattr(trading, "data_freshness", lambda *a, **k: (True, "ok"))


def test_scan_ranks_and_explains_decisions(c, db):
    r = c.post("/api/opportunities/scan").json()
    items = r["items"]
    assert r["setups"] == len(st.STRATEGIES) and len(items) == len(st.STRATEGIES)
    order = [op.DECISION_ORDER[x["decision"]] for x in items]
    assert order == sorted(order)
    x = items[0]
    assert x["symbol"] == "EURUSD" and x["side"] == "SELL" and 0 <= x["score"] <= 100 and x["evidence"]
    assert x["route"]["kind"] == "practice" and x["history"]["level"] == "unknown"
    assert {i["ok"] for i in x["evidence"]} <= {"pass", "warn", "fail"}
    if x["economics"]["available"]:
        assert x["economics"]["net_stop"] >= x["economics"]["gross_stop"]
    assert c.post("/api/opportunities/scan").status_code == 429


def test_queue_goes_to_approval_then_not_twice(c, db):
    items = c.get("/api/opportunities").json()["items"]
    cand = next((x for x in items if x["decision"] == "TRADE CANDIDATE"), None) or items[0]
    r = c.post(f"/api/opportunities/{cand['key']}/queue").json()
    assert r["ok"], r
    if r["ok"]:
        assert "Setups to approve" in r["detail"]
        assert db.scalar(select(SignalApproval).where(SignalApproval.signal_id == r["signal_id"])).status == "WAITING"
        assert c.post(f"/api/opportunities/{cand['key']}/queue").json()["detail"] == "Already sent"
    else:
        assert "Re-check" in r["detail"] or "switched off" in r["detail"]
    assert c.post("/api/opportunities/nope/queue").json()["ok"] is False


def test_evidence_levels_and_ai_analyst(c, db, monkeypatch):
    from app.models import RaceTrade
    for k in range(31):
        db.add(RaceTrade(strategy="rsi2_meanrev", symbol="EURUSD", timeframe="H1", side="BUY", candle_time=utcnow() - timedelta(hours=k + 2),
                         entry=1, stop_loss=0.99, take_profit=1.02, status="WIN", r=0.5))
    db.commit()
    assert op.evidence(db, "rsi2_meanrev", "EURUSD", "H1")["level"] == "proven"
    assert op.evidence(db, "bb_squeeze", "EURUSD", "H1")["level"] == "unknown"
    a = state.get(db, "ai")
    a.update(enabled=True, api_key_enc=encrypt("sk-ant-x"), provider="anthropic", model="claude-sonnet-5-5")
    state.put(db, "ai", a)
    db.commit()
    from app.routers import ai as ai_router
    monkeypatch.setattr(ai_router, "_call", lambda p, m, k, s, msgs: "SUPPORTING EVIDENCE ...")
    key = c.get("/api/opportunities").json()["items"][0]["key"]
    assert c.post("/api/opportunities/explain", json={"key": key}).json()["analysis"].startswith("SUPPORTING")
    assert c.put("/api/opportunities/config", json={"auto_proven": True}).json()["config"]["auto_proven"] is True
