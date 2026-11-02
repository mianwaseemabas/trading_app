"""Master-plan features: data trust, scoring/economics, outcomes, alerts, live gate evidence, pilot preset."""
import os
from datetime import datetime, timedelta, timezone

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import (Alert, AlertDelivery, Candle, Quote, Signal, SignalAssessment, SignalOutcome, User,  # noqa: E402
                        utcnow)
from app.security import hash_password  # noqa: E402
from app.services import alerts, assess, livegate, quality, trading  # noqa: E402
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
    for _ in range(3):
        md.run_simulator(db, ["EURUSD", "GBPUSD"], ["M15", "H1"])
    state.put(db, "news_status", {"ok": True, "last_fetch": utcnow().isoformat(), "error": "", "count": 0})
    db.commit()
    db.close()
    yield


@pytest.fixture(autouse=True)
def open_market(monkeypatch):
    monkeypatch.setattr(trading, "data_freshness", lambda *a, **k: (True, "ok (test)"))
    monkeypatch.setattr(quality, "session_state", lambda *a, **k: "OPEN")


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
    return cl


def test_quote_quality_pips_weekend_and_wide(db, monkeypatch):
    q = db.get(Quote, "EURUSD")
    q.bid, q.ask, q.collected_at = 1.08000, 1.08012, utcnow()
    db.commit()
    r = quality.quote_quality(db, "EURUSD", set())
    assert r["spread_pips"] == pytest.approx(1.2) and r["quality"] == "OK" and r["tradeable"]
    q.ask = 1.08070  # 7 pips, like the screenshot
    db.commit()
    r = quality.quote_quality(db, "EURUSD", set())
    assert r["quality"] == "WIDE_SPREAD" and not r["tradeable"] and "7.0 pips" in r["issues"][0]
    monkeypatch.undo()
    sat = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
    assert quality.session_state("EURUSD", False, None, sat) == "CLOSED_WEEKEND"
    assert quality.session_state("BTCUSDT", True, None, sat) == "OPEN"
    monkeypatch.setattr(quality, "session_state", lambda *a, **k: "CLOSED_WEEKEND")
    r = quality.quote_quality(db, "EURUSD", set())
    assert r["quality"] == "CLOSED" and "weekend" in r["explanation"]
    q.ask = 1.08012
    q.collected_at = utcnow() - timedelta(minutes=10)
    db.commit()
    monkeypatch.setattr(quality, "session_state", lambda *a, **k: "OPEN")
    assert quality.quote_quality(db, "EURUSD", set())["quality"] == "STALE"
    q.collected_at = utcnow()
    db.commit()


def test_data_health_accounts_and_risk_status(c):
    h = c.get("/api/data/health").json()
    assert {x["symbol"] for x in h["quotes"]} >= {"EURUSD", "GBPUSD"} and h["feeds"][0]["name"] == "Data source"
    acc = c.get("/api/accounts").json()["accounts"]
    envs = {a["env"] for a in acc}
    assert {"APP_PAPER", "CRYPTO_PAPER"} <= envs and all("source" in a and "currency" in a for a in acc)
    rs = c.get("/api/risk/status").json()
    assert rs["risk_per_trade"] > 0 and "no_trade_reasons" in rs and rs["max_trades_per_day"] == 3


def make_signal(db, side="BUY"):
    q = db.get(Quote, "EURUSD")
    last = md.load_candles(db, "EURUSD", "H1", limit=1).index[-1].to_pydatetime()
    entry = q.ask if side == "BUY" else q.bid
    sl = entry - 0.0012 if side == "BUY" else entry + 0.0012
    tp = entry + 0.0030 if side == "BUY" else entry - 0.0030
    s = Signal(symbol="EURUSD", timeframe="H1", candle_time=last, strategy="trend_pullback", strategy_version=f"t{utcnow().timestamp()}",
               action=side, entry_ref=entry, stop_loss=round(sl, 5), take_profit=round(tp, 5), features={}, reasons=[], status="SIGNAL_ONLY",
               trading_mode="PAPER")
    db.add(s)
    db.commit()
    return s


def test_assessment_score_gates_economics(c, db, monkeypatch):
    s = make_signal(db)
    monkeypatch.setattr(assess, "utcnow", lambda: s.candle_time + timedelta(minutes=70))
    a = assess.assess(db, s)
    db.commit()
    assert 0 <= a.score <= 100 and len(a.breakdown) == 6 and sum(b["max"] for b in a.breakdown) == 100
    assert a.verdict in ("NO_TRADE", "WATCH", "PAPER_TEST", "RESEARCH_SUPPORTED") and a.reliability == "INSUFFICIENT_EVIDENCE"
    e = a.economics
    assert e["available"] and e["net_stop"] >= e["gross_stop"] and e["net_target"] <= e["gross_target"]
    assert e["net_rr"] < e["gross_rr"] and 0 < e["break_even_win_rate_pct"] < 100
    assert e["net_stop"] <= e["risk_budget"] + 0.01  # total planned loss incl. costs within budget
    assert e["comparable_strategy"]["p_win"] == "UNKNOWN"  # never derived from the score
    r = c.get(f"/api/signals/{s.id}/assessment").json()
    assert r["score"] == a.score and "NOT a probability" in r["score_note"]
    board = c.get("/api/signal-board?tab=all").json()
    assert any(x["id"] == s.id and x["score"] == a.score for x in board["signals"])
    # a data problem is a hard NO_TRADE
    q = db.get(Quote, "EURUSD")
    q.ask = q.bid + 0.0009
    db.commit()
    a2 = assess.assess(db, s, save=False)
    db.rollback()
    assert a2.verdict == "NO_TRADE" and a2.data["data_blocked"], (a2.data, a2.gates[0])
    q.ask = q.bid + 0.0001
    db.commit()


def test_shadow_outcomes_and_strategy_compare(c, db):
    s = make_signal(db)
    # put the signal a few hours back so the "future" candles are already in the past on weekdays too
    s.candle_time = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    db.commit()
    start = s.candle_time + timedelta(hours=1)
    # synthetic future candles: the target is hit on the 2nd candle
    rows = [{"time": start, "open": s.entry_ref, "high": s.entry_ref + 0.0005, "low": s.entry_ref - 0.0004, "close": s.entry_ref + 0.0003},
            {"time": start + timedelta(hours=1), "open": s.entry_ref + 0.0003, "high": s.take_profit + 0.0002, "low": s.entry_ref, "close": s.take_profit}]
    for r in rows:
        db.merge(Candle(symbol="EURUSD", timeframe="H1", time=r["time"], open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                        tick_volume=1, spread_points=0, source="test", collected_at=utcnow()))
    db.commit()
    assess.track_outcomes(db)
    db.commit()
    o = db.get(SignalOutcome, s.id)
    assert o.status == "TARGET" and o.r_gross == pytest.approx(2.5, abs=0.05) and not o.traded
    cmp = c.get("/api/strategies/compare").json()
    tp = next(x for x in cmp["strategies"] if x["strategy"] == "trend_pullback")
    assert tp["n"] >= 1 and tp["reliability"] == "INSUFFICIENT_EVIDENCE"


def test_alerts_dedupe_audit_bridge_and_delivery(c, db):
    assert alerts.raise_alert(db, "DATA_STALE", "x", dedup="same-key") is not None
    assert alerts.raise_alert(db, "DATA_STALE", "x", dedup="same-key") is None
    db.commit()
    r = c.post("/api/live/order", json={"symbol": "EURUSD", "side": "BUY", "stop_loss": round(db.get(Quote, "EURUSD").bid - 0.0015, 5),
                                        "sizing_mode": "auto"})
    assert r.status_code == 200, r.text
    lst = c.get("/api/alerts").json()
    assert any(a["type"] == "ORDER_FILLED" for a in lst["alerts"]) and lst["unread"] >= 1
    pr = state.get(db, "alert_prefs")
    pr["channels"]["telegram"] = ["ORDER_FILLED"]
    state.put(db, "alert_prefs", pr)
    db.commit()
    alerts.deliver_pending(db)
    db.commit()
    d = db.scalars(select(AlertDelivery)).all()
    assert d and all(x.status == "skipped" for x in d)  # no Telegram/email configured -> recorded, not retried forever
    assert c.post("/api/alerts/read", json={"all": True}).json()["unread"] == 0
    p = c.post("/api/alerts/preferences", json={"channels": {"telegram": ["STOP_HIT", "BOGUS"]}, "telegram_chat_id": "123"}).json()
    assert p["channels"]["telegram"] == ["STOP_HIT"] and p["telegram"]["chat_id"] == "123"
    for pos in c.get("/api/positions?status=OPEN").json():
        c.post(f"/api/positions/{pos['id']}/close")


def test_max_trades_per_day_and_pilot_preset(c, db):
    r = c.post("/api/system/risk/pilot-preset").json()
    assert r["risk"]["risk_per_trade_pct"] == 0.25 and r["risk"]["daily_loss_pct"] == 1.0 and r["require_approval"]
    risk = state.get(db, "risk")
    risk["max_trades_per_day"] = 1
    state.put(db, "risk", risk)
    db.commit()
    pv = c.post("/api/live/preview", json={"symbol": "EURUSD", "side": "BUY", "stop_loss": round(db.get(Quote, "EURUSD").bid - 0.0015, 5)}).json()
    assert any(x["check"].startswith("new trades today") and not x["passed"] for x in pv["checks"])
    risk["max_trades_per_day"] = 3
    state.put(db, "risk", risk)
    db.commit()


def test_live_gate_needs_evidence_and_expires(c, db):
    res = livegate.self_test()
    assert all(r["passed"] for r in res), [r for r in res if not r["passed"]]
    assert c.post("/api/system/live-gate/self-test").json()["passed"]
    items = {k: True for k in c.get("/api/system/live-gate").json()["items"]}
    r = c.put("/api/system/live-gate", json={"items": items, "notes": {"broker_legal_checked": "x"}, "approve": True, "password": PW})
    assert r.status_code == 400 and "Evidence missing" in r.json()["detail"]
    g = state.get(db, "live_gate")
    g.update(approved=True, expires_at=(utcnow() - timedelta(days=1)).isoformat())
    state.put(db, "live_gate", g)
    db.commit()
    assert not livegate.is_approved(db)
    r = c.post("/api/system/mode", json={"mode": "MT5_LIVE", "password": PW, "confirm": "ENABLE LIVE TRADING"})
    assert r.status_code == 400
