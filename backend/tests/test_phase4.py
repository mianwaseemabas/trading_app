"""Saudi watch-only stocks, commodity/index news mapping, Academy streak + readiness exam + certificate."""
import os
import time
from datetime import timedelta

import pytest

from app.services.instruments_meta import headline_assets, news_currencies


def test_news_mapping_for_metals_oil_indices():
    assert news_currencies("XAUUSD") == ["USD"] and news_currencies("OIL_CRUDE") == ["USD"]
    assert news_currencies("GER40") == ["EUR"] and news_currencies("GBPJPY") == ["GBP", "JPY"]
    assert {"GOLD", "USD"} <= headline_assets("GOLD") and "INDICES" in headline_assets("US500")


if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import AcademyDay, Quote, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import tadawul  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services.academy_content import all_lessons  # noqa: E402

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


def fake_yahoo(url):
    now = int(time.time())
    if "interval=60m" in url:
        ts = [now - 3600 * k for k in range(30, 1, -1)]
    else:
        ts = [now - 86400 * k for k in range(60, 1, -1)]
    n = len(ts)
    return {"chart": {"result": [{"meta": {"currency": "SAR", "regularMarketPrice": 27.5, "regularMarketTime": now, "longName": "Saudi Arabian Oil Company"},
                                  "timestamp": ts, "indicators": {"quote": [{"open": [27.0] * n, "high": [27.8] * n, "low": [26.9] * n,
                                                                             "close": [27.4] * n, "volume": [1000] * n}]}}]}}


def test_tadawul_watch_only(c, db):
    assert c.put("/api/tadawul", json={"symbols": ["AAPL"]}).status_code == 400
    r = c.put("/api/tadawul", json={"symbols": ["2222.SR", "^TASI.SR"]}).json()
    assert r["symbols"] == ["2222.SR", "^TASI.SR"] and "watch" in r["note"].lower()
    st = tadawul.refresh(db, force=True, http=fake_yahoo)
    db.commit()
    assert st["status"]["2222.SR"]["ok"] and st["status"]["2222.SR"]["name"] == "Saudi Arabian Oil Company"
    assert db.get(Quote, "2222.SR").bid == 27.5 and len(md.load_candles(db, "2222.SR", "D1")) > 50
    wl = c.get("/api/intel/watchlist").json()
    row = next(w for w in wl if w["symbol"] == "2222.SR")
    assert row["watch_only"] and row["asset"] == "saudi stock" and row["bid"] == 27.5
    assert c.get("/api/intel/terminal?symbol=2222.SR").json()["venue"] == "watch"
    assert len(c.get("/api/intel/candles?symbol=2222.SR&tf=D1&limit=40").json()["candles"]) == 40
    tr = state.get(db, "trading")
    tr["mode"] = "PAPER"
    state.put(db, "trading", tr)
    db.commit()
    pv = c.post("/api/live/preview", json={"symbol": "2222.SR", "side": "BUY", "stop_loss": 26.0}).json()
    assert any(x["check"].startswith("market is tradable") and not x["passed"] for x in pv["checks"])


def test_streak_exam_certificate(c, db):
    from app.services.academy_content import lesson
    c.post("/api/academy/lessons/start-tour/steps", json={"step": 0, "done": True})
    me = db.query(User).filter_by(username="op").one()
    db.add(AcademyDay(user_id=me.id, day=(utcnow() - timedelta(days=1)).astimezone().strftime("%Y-%m-%d")))
    db.commit()
    ov = c.get("/api/academy").json()
    assert ov["streak"]["current"] >= 1 and ov["streak"]["today"] and not ov["certificate"]
    ex = c.post("/api/academy/exam/start").json()
    assert len(ex["questions"]) == 15 and "answer" not in ex["questions"][0]
    wrong = c.post(f"/api/academy/exam/{ex['exam_id']}/submit", json={"answers": {}}).json()
    assert not wrong["passed"] and wrong["score"] == 0 and wrong["results"][0]["why"]
    assert c.post(f"/api/academy/exam/{ex['exam_id']}/submit", json={"answers": {}}).status_code == 409
    ex = c.post("/api/academy/exam/start").json()
    answers = {}
    for q in ex["questions"]:
        lid, i = q["id"].rsplit(":", 1)
        answers[q["id"]] = lesson(lid)[1]["quiz"][int(i)]["answer"]
    ok = c.post(f"/api/academy/exam/{ex['exam_id']}/submit", json={"answers": answers}).json()
    assert ok["passed"] and ok["score"] == 100
    assert c.get("/api/academy/certificate").status_code == 400  # lessons not finished yet
    for _, l in all_lessons():
        n = len(l.get("quiz", []))
        if n:
            c.post(f"/api/academy/lessons/{l['id']}/quiz", json={"answers": [q["answer"] for q in l["quiz"]]})
        assert c.post(f"/api/academy/lessons/{l['id']}/complete").status_code == 200
    cert = c.get("/api/academy/certificate").json()
    assert cert["score"] == 100 and cert["cert_id"].startswith("QVX-") and c.get("/api/academy").json()["certificate"]
    ev = c.get("/api/system/live-gate").json()["evidence"]["academy_exam_passed"]
    assert ev["passed"]


def test_security_checklist(c):
    r = c.get("/api/security/checklist").json()
    keys = {i["key"]: i for i in r["items"]}
    assert r["total"] == len(r["items"]) >= 12 and "security-check.sh" in r["server_check"]
    assert keys["2fa_admins"]["ok"] is False and keys["live_locked"]["ok"] is True
