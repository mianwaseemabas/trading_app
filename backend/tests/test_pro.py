"""Pro trading features: sizing modes, limit/stop orders, SL/TP editing, trade management, controls, news."""
import os
from datetime import timedelta

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import PendingOrder, Position, PositionMgmt, Quote, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services import news, pro, trading  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False))
    db.commit()
    md.run_simulator(db, ["EURUSD", "GBPUSD"], ["M15", "H1"])
    db.commit()
    db.close()
    yield


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr(trading, "data_freshness", lambda *a, **k: (True, "ok (test)"))
    monkeypatch.setattr(__import__("app.services.quality", fromlist=["x"]), "session_state", lambda *a, **k: "OPEN")


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
    for p in cl.get("/api/live/pending").json():
        cl.post(f"/api/live/pending/{p['id']}/cancel")
    cl.put("/api/live/controls", json={"auto_trading": True, "manual_trading": True, "disabled_symbols": [], "news_filter": True,
                                       "session_filter": False})
    cl.post("/api/system/unlock", json={"note": "test cleanup"})


def set_quote(db, symbol, bid, spread=0.0001):
    q = db.get(Quote, symbol)
    q.bid, q.ask, q.collected_at = round(bid, 5), round(bid + spread, 5), utcnow()
    db.commit()


def ticket(db, **kw):
    q = db.get(Quote, "EURUSD")
    t = {"symbol": "EURUSD", "side": "BUY", "stop_loss": round(q.bid - 0.0020, 5), "take_profit": round(q.ask + 0.0040, 5)}
    t.update(kw)
    return t


def test_sizing_modes_and_outcomes(c, db):
    pv = c.post("/api/live/preview", json=ticket(db)).json()
    assert pv["approved"] and pv["loss_if_sl"] <= 2.5 and pv["gain_if_tp"] > pv["loss_if_sl"] and pv["reward_risk"] > 1.5
    # Risk a chosen dollar amount below the policy budget.
    pv = c.post("/api/live/preview", json=ticket(db, sizing_mode="risk_amount", sizing_value=1.0)).json()
    assert not pv["approved"] or pv["risk_amount"] <= 1.0
    # Above the policy budget -> refused.
    pv = c.post("/api/live/preview", json=ticket(db, sizing_mode="risk_amount", sizing_value=50)).json()
    assert not pv["approved"] and any("chosen size" in x["check"] and not x["passed"] for x in pv["checks"])
    pv = c.post("/api/live/preview", json=ticket(db, sizing_mode="lots", sizing_value=1.0)).json()
    assert not pv["approved"]


def test_limit_order_triggers_and_manages(c, db):
    q = db.get(Quote, "EURUSD")
    trig = round(q.ask - 0.0010, 5)
    bad = c.post("/api/live/order", json=ticket(db, order_type="LIMIT", trigger_price=round(q.ask + 0.001, 5)))
    assert bad.status_code == 400 and "below" in bad.json()["detail"]
    r = c.post("/api/live/order", json=ticket(db, order_type="LIMIT", trigger_price=trig, stop_loss=round(trig - 0.0020, 5),
                                               take_profit=round(trig + 0.0040, 5), management={"breakeven_r": 1.0}))
    assert r.status_code == 200 and r.json()["pending_id"], r.text
    pid = r.json()["pending_id"]
    pro.process_pending(db)
    db.commit()
    assert db.get(PendingOrder, pid).status == "WORKING"  # price not reached yet
    set_quote(db, "EURUSD", trig - 0.0002)
    pro.process_pending(db)
    db.commit()
    db.expire_all()
    po = db.get(PendingOrder, pid)
    assert po.status == "TRIGGERED", po.error
    pos = db.scalar(select(Position).where(Position.status == "OPEN", Position.symbol == "EURUSD"))
    m = db.get(PositionMgmt, pos.id)
    assert m and m.breakeven_r == 1.0
    # Price moves +1R -> stop moves to entry (breakeven), only ever tightened.
    risk = pos.open_price - m.initial_sl
    set_quote(db, "EURUSD", pos.open_price + risk * 1.1)
    trading.update_paper_positions(db)
    pro.manage_positions(db)
    db.commit()
    db.expire_all()
    pos = db.get(Position, pos.id)
    assert pos.stop_loss == pytest.approx(pos.open_price) and db.get(PositionMgmt, pos.id).breakeven_done
    d = c.get(f"/api/positions/{pos.id}/detail").json()
    assert any(t["action"] == "position.sl_tp_modified" for t in d["timeline"]) and "candles" in d  # empty at weekends


def test_modify_tighten_only(c, db):
    set_quote(db, "EURUSD", 1.08)
    r = c.post("/api/live/order", json=ticket(db))
    assert r.status_code == 200, r.text
    pos = db.scalar(select(Position).where(Position.status == "OPEN", Position.symbol == "EURUSD"))
    wider = c.post(f"/api/positions/{pos.id}/modify", json={"stop_loss": round(pos.stop_loss - 0.001, 5)})
    assert wider.status_code == 400 and "tightened" in wider.json()["detail"]
    ok = c.post(f"/api/positions/{pos.id}/modify", json={"stop_loss": round(pos.stop_loss + 0.0005, 5), "take_profit": 1.09})
    assert ok.status_code == 200 and ok.json()["status"] == "applied"
    assert c.put(f"/api/positions/{pos.id}/note", json={"note": "good setup", "tags": ["trend"], "rating": 4}).status_code == 200


def test_controls_switches(c, db):
    set_quote(db, "EURUSD", 1.08)
    c.put("/api/live/controls", json={"manual_trading": False})
    r = c.post("/api/live/order", json=ticket(db))
    assert r.status_code == 400 and "Manual trading is switched off" in r.json()["detail"]
    c.put("/api/live/controls", json={"manual_trading": True, "disabled_symbols": ["EURUSD"]})
    pv = c.post("/api/live/preview", json=ticket(db)).json()
    assert not pv["approved"] and any(x["check"] == "pair enabled for trading" and not x["passed"] for x in pv["checks"])
    c.put("/api/live/controls", json={"disabled_symbols": []})


def test_news_parse_and_filter(c, db, monkeypatch):
    soon = (utcnow() + timedelta(minutes=10)).isoformat()
    rows = news.parse([{"title": "Non-Farm Employment Change", "country": "USD", "date": soon, "impact": "High", "forecast": "180K",
                        "previous": "175K"},
                       {"title": "German ZEW", "country": "EUR", "date": "2026-11-03T05:00:00-05:00", "impact": "Medium"},
                       {"bad": "row"}])
    assert len(rows) == 2 and rows[1]["time"].hour == 10  # converted to UTC
    monkeypatch.setattr(news, "_get", lambda url: [{"title": "Non-Farm Employment Change", "country": "USD", "date": soon,
                                                    "impact": "High", "forecast": "180K", "previous": "175K"}])
    news.refresh(db, force=True)
    assert state.get(db, "news_status")["ok"]
    pv = c.post("/api/live/preview", json=ticket(db)).json()  # EURUSD contains USD -> blocked
    chk = [x for x in pv["checks"] if x["check"] == "no high-impact news window"][0]
    assert not chk["passed"] and "Non-Farm" in chk["detail"]
    c.put("/api/live/controls", json={"news_filter": False})
    pv = c.post("/api/live/preview", json=ticket(db)).json()
    assert [x for x in pv["checks"] if x["check"] == "no high-impact news window"][0]["passed"]
    n = c.get("/api/news").json()
    assert any(e["title"] == "Non-Farm Employment Change" for e in n["events"])
    feed = c.get("/api/live/feed").json()
    assert feed["news"] and "controls" in feed


def test_export_csv(c, db):
    set_quote(db, "EURUSD", 1.08)
    c.post("/api/live/order", json=ticket(db))
    for p in c.get("/api/positions?status=OPEN").json():
        c.post(f"/api/positions/{p['id']}/close")
    r = c.get("/api/positions/export.csv")
    assert r.status_code == 200 and r.text.startswith("id,mode,symbol") and "EURUSD" in r.text
    perf = c.get("/api/performance?mode=PAPER").json()
    assert "breakdown" in perf and "manual" in perf["breakdown"]["source"]
