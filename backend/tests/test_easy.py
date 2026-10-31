"""Easy mode prefs, Home summary, Autopilot start/stop."""
import os

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.automation import auto_ok, flags_of  # noqa: E402

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


@pytest.fixture
def c():
    cl = TestClient(app)
    r = cl.post("/api/auth/login", json={"username": "op", "password": PW})
    cl.headers["x-csrf-token"] = r.json()["user"]["csrf_token"]
    return cl


def test_prefs_default_easy_and_saved(c):
    assert c.get("/api/me/prefs").json() == {"mode": "easy", "explain": True}
    assert c.put("/api/me/prefs", json={"mode": "pro"}).status_code == 400
    assert c.put("/api/me/prefs", json={"mode": "advanced", "explain": False}).json() == {"mode": "advanced", "explain": False}
    assert c.get("/api/me/prefs").json()["mode"] == "advanced"


def test_home_summary(c):
    h = c.get("/api/home").json()
    assert len(h["lights"]) == 6 and {l["state"] for l in h["lights"]} <= {"ok", "bad", "off"}
    assert h["autopilot"]["on"] is False and "readiness" in h and h["today"]["open"] == 0
    assert any("App engine" in n["text"] for n in h["needs"])  # worker never ran in tests -> shown as a problem


def test_autopilot_start_stop_and_live_refusal(c, db):
    plan = c.get("/api/autopilot").json()
    assert plan["plan"] and plan["accounts"] == ["PAPER"] and not plan["blocked"]
    r = c.post("/api/autopilot/start").json()
    assert r["on"]
    db.expire_all()
    risk = state.get(db, "risk")
    assert risk["max_trades_per_day"] == 10 and risk["risk_per_trade_pct"] == 0.25 and risk["max_concurrent_positions"] == 3
    assert flags_of(state.get(db, "controls"))["PAPER"] and not flags_of(state.get(db, "controls"))["MT5_LIVE"]
    assert state.get(db, "news_auto")["breakout"] and state.get(db, "ai_jobs")["brief_enabled"]
    assert "AUTO_TRADE" in state.get(db, "alert_prefs")["channels"]["push"]
    assert state.get(db, "trading")["mode"] == "PAPER"
    assert c.get("/api/home").json()["autopilot"]["accounts"] == ["PAPER"]
    r = c.post("/api/autopilot/stop").json()
    db.expire_all()
    assert not r["on"] and not auto_ok(db, "PAPER") and not state.get(db, "news_auto")["breakout"]
    cc = state.get(db, "crypto")
    cc["mode"] = "LIVE"
    state.put(db, "crypto", cc)
    db.commit()
    assert "practice only" in c.get("/api/autopilot").json()["blocked"]
    assert c.post("/api/autopilot/start").status_code == 400
    cc["mode"] = "OFF"
    state.put(db, "crypto", cc)
    db.commit()
