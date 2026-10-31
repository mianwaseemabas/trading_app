"""Academy content integrity, progress/quiz API, tutor; chart timeframes from ticks."""
import os
from datetime import timedelta

import pytest

from app.services.academy_content import COURSES, all_lessons


def test_content_is_consistent():
    ids = [l["id"] for _, l in all_lessons()]
    assert len(ids) == len(set(ids)) and len(ids) >= 25
    for c, l in all_lessons():
        assert l["title"] and l["goal"] and l["explain"] and l["steps"], l["id"]
        for q in l.get("quiz", []):
            assert 0 <= q["answer"] < len(q["options"]) and q["why"], (l["id"], q["q"])
        for s in l["steps"]:
            assert s["text"] and (not s.get("link") or s["link"].startswith("#/"))
    assert all(c["lessons"] for c in COURSES)


if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import chart  # noqa: E402

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
def c():
    cl = TestClient(app)
    r = cl.post("/api/auth/login", json={"username": "op", "password": PW})
    cl.headers["x-csrf-token"] = r.json()["user"]["csrf_token"]
    return cl


def test_progress_quiz_and_complete(c):
    ov = c.get("/api/academy").json()
    assert ov["done"] == 0 and ov["next"]["id"] == "start-tour" and any(x["key"] == "telegram" for x in ov["checks"])
    l = c.get("/api/academy/lessons/start-tour").json()
    assert l["next"] == "start-secure" and "answer" not in l["quiz"][0]
    assert c.post("/api/academy/lessons/start-tour/steps", json={"step": 0, "done": True}).json()["steps_done"] == [0]
    assert c.post("/api/academy/lessons/start-tour/complete").status_code == 400  # quiz first
    bad = c.post("/api/academy/lessons/start-tour/quiz", json={"answers": [0, 0]}).json()
    assert not bad["passed"] and bad["results"][0]["answer"] == 1
    good = c.post("/api/academy/lessons/start-tour/quiz", json={"answers": [1, 1]}).json()
    assert good["passed"] and good["score"] == 100
    assert c.post("/api/academy/lessons/start-tour/complete").status_code == 200
    ov = c.get("/api/academy").json()
    assert ov["done"] == 1 and ov["next"]["id"] == "start-secure"
    assert c.get("/api/academy/lessons/nope").status_code == 404


def test_tutor_needs_ai_then_answers(c, monkeypatch):
    assert c.post("/api/academy/tutor", json={"lesson_id": "basics-sltp", "messages": [{"role": "user", "content": "hi"}]}).status_code == 400
    from app import state
    from app.routers import ai as ai_router
    from app.secretbox import encrypt
    db = SessionLocal()
    a = state.get(db, "ai")
    a.update(enabled=True, api_key_enc=encrypt("sk-ant-x"), provider="anthropic", model="claude-sonnet-5-5")
    state.put(db, "ai", a)
    db.commit()
    db.close()
    seen = {}
    monkeypatch.setattr(ai_router, "_call", lambda p, m, k, system, msgs: seen.update(system=system) or "Stop-loss is your seat belt.")
    r = c.post("/api/academy/tutor", json={"lesson_id": "basics-sltp", "messages": [{"role": "user", "content": "what is a stop?"}]})
    assert r.status_code == 200 and "seat belt" in r.json()["reply"]
    assert "CURRENT LESSON" in seen["system"] and "Stop-loss, take-profit" in seen["system"] and "SETUP STATUS" in seen["system"]


def test_seconds_chart_from_ticks(c):
    db = SessionLocal()
    t0 = utcnow().replace(microsecond=0) - timedelta(seconds=120)
    for k in range(120):
        chart._last_tick.pop("EURUSD", None)
        chart.record_tick(db, "EURUSD", 1.1 + k * 1e-5, 1.1001 + k * 1e-5, t0 + timedelta(seconds=k))
    db.commit()
    db.close()
    r = c.get("/api/intel/candles?symbol=EURUSD&tf=S5&limit=20").json()
    assert r["source"] == "live price updates" and len(r["candles"]) >= 20 and r["seconds"] == 5
    x = r["candles"][-2]
    assert x["high"] >= max(x["open"], x["close"]) and x["low"] <= min(x["open"], x["close"])
    assert c.get("/api/intel/candles?symbol=EURUSD&tf=X9").status_code == 400
