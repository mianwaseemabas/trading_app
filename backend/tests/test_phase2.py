"""AI brief/coach, ask-the-chart, Telegram bot commands with PIN, health monitor, off-server backups."""
import os
from datetime import timedelta

import pytest

from app import backup_tool


def test_backup_encrypt_roundtrip():
    blob = backup_tool.encrypt_bytes(b"pg dump data" * 100, "correct horse battery")
    assert blob.startswith(b"QVXB1") and backup_tool.decrypt_bytes(blob, "correct horse battery") == b"pg dump data" * 100
    with pytest.raises(Exception):
        backup_tool.decrypt_bytes(blob, "wrong password!")


if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Alert, DataIncident, Signal, SignalApproval, User, utcnow  # noqa: E402
from app.secretbox import encrypt  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import ai_jobs, health, tgbot  # noqa: E402
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
        md.run_simulator(db, ["EURUSD"], ["H1"])
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


def test_migration_added_new_alert_types(db):
    ch = state.get(db, "alert_prefs")["channels"]
    assert "AI_BRIEF" in ch["telegram"] and "HEALTH" in ch["push"]


def test_brief_and_coach_without_ai(c, db):
    r = c.post("/api/ai-jobs/brief").json()
    assert "TODAY'S RISK EVENTS" in r["text"] and "EURUSD" in r["text"]
    r = c.post("/api/ai-jobs/coach").json()
    assert "No closed trades" in r["text"]
    types = [a.type for a in db.scalars(select(Alert)).all()]
    assert "AI_BRIEF" in types and "AI_COACH" in types
    assert c.put("/api/ai-jobs", json={"brief_time": "25:00"}).status_code == 400
    assert c.put("/api/ai-jobs", json={"timezone": "Mars/Base"}).status_code == 400
    j = c.put("/api/ai-jobs", json={"brief_enabled": True, "brief_time": "00:00", "coach_enabled": True}).json()
    assert j["brief_enabled"] and j["brief_time"] == "00:00"


def test_scheduler_sends_brief_once_per_day(db):
    n0 = len(db.scalars(select(Alert).where(Alert.type == "AI_BRIEF")).all())
    ai_jobs.scheduled(db)
    db.commit()
    ai_jobs.scheduled(db)
    db.commit()
    assert len(db.scalars(select(Alert).where(Alert.type == "AI_BRIEF")).all()) == n0 + 1


def test_ai_brief_uses_claude_when_on(c, db, monkeypatch):
    a = state.get(db, "ai")
    a.update(enabled=True, api_key_enc=encrypt("sk-ant-x"), provider="anthropic", model="claude-sonnet-5-5")
    state.put(db, "ai", a)
    db.commit()
    from app.routers import ai as ai_router
    from app.routers import assist
    assist._rate.clear()
    got = {}
    monkeypatch.setattr(ai_router, "_call", lambda p, m, k, system, msgs: got.update(system=system, prompt=msgs[0]["content"]) or "AI BRIEF TEXT")
    assert c.post("/api/ai-jobs/brief").json()["text"] == "AI BRIEF TEXT"
    assert "TODAY'S RISK EVENTS" in got["system"] and "markets" in got["prompt"]
    r = c.post("/api/intel/chart-explain", json={"symbol": "EURUSD", "tf": "H1"})
    assert r.status_code == 200 and r.json()["explanation"] == "AI BRIEF TEXT" and "REASONS IT COULD GO DOWN" in got["prompt"]
    a["enabled"] = False
    state.put(db, "ai", a)
    db.commit()


def _signal(db):
    q = md.latest_quotes(db) if hasattr(md, "latest_quotes") else None  # noqa: F841
    from app.models import Quote
    px = db.get(Quote, "EURUSD").ask
    s = Signal(symbol="EURUSD", timeframe="H1", candle_time=utcnow().replace(minute=0, second=0, microsecond=0), strategy="trend_pullback",
               strategy_version=f"t{int(utcnow().timestamp())}", action="BUY", entry_ref=px, stop_loss=round(px - 0.0015, 5),
               take_profit=round(px + 0.003, 5), features={}, reasons=[], status="WAITING", trading_mode="PAPER")
    db.add(s)
    db.flush()
    db.add(SignalApproval(signal_id=s.id, status="WAITING", expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    return s


def test_bot_commands_and_pin(c, db, monkeypatch):
    assert c.put("/api/tgbot", json={"pin": "1111", "password": PW}).status_code == 400
    assert c.put("/api/tgbot", json={"pin": "4826", "password": "wrong"}).status_code == 403
    assert c.put("/api/tgbot", json={"pin": "4826", "password": PW, "enabled": True}).json()["has_pin"]
    assert "/approve" in tgbot.handle(db, "/help")[0]
    assert "Mode:" in tgbot.handle(db, "/status")[0]
    s = _signal(db)
    out, delete = tgbot.handle(db, f"/approve {s.id} 0000")
    assert "Wrong PIN" in out and delete
    from app.services import quality, trading
    monkeypatch.setattr(trading, "data_freshness", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(quality, "session_state", lambda *a, **k: "OPEN")
    tr = state.get(db, "trading")
    tr["mode"] = "PAPER"
    state.put(db, "trading", tr)
    db.commit()
    out, delete = tgbot.handle(db, f"/approve {s.id} 4826")
    db.commit()
    assert delete and "approved" in out, out
    out, _ = tgbot.handle(db, "/kill 4826")
    db.commit()
    assert "KILL SWITCH" in out and state.get(db, "trading")["kill_switch"]
    tgbot.handle(db, "/resume 4826")
    db.commit()
    assert not state.get(db, "trading")["kill_switch"]
    assert "Unknown command" in tgbot.handle(db, "/whatever")[0]


def test_bot_poll_only_answers_own_chat(db):
    p = state.get(db, "alert_prefs")
    p["telegram"].update(bot_token_enc=encrypt("1:abc"), chat_id="42")
    state.put(db, "alert_prefs", p)
    db.commit()
    sent = []

    def api(token, method, payload):
        if method == "getUpdates":
            return {"result": [{"update_id": 7, "message": {"message_id": 1, "chat": {"id": 99}, "text": "/status"}},
                               {"update_id": 8, "message": {"message_id": 2, "chat": {"id": 42}, "text": "/kill 0000"}}]}
        sent.append((method, payload))
        return {}
    assert tgbot.poll_once(api, 0, timeout=0) == 9
    assert [m for m, _ in sent] == ["deleteMessage", "sendMessage"] and sent[1][1]["chat_id"] == "42"


def test_health_monitor_after_5_minutes_and_recovery(c, db):
    inc = DataIncident(key="DATA_STALE:EURUSD", kind="DATA_STALE", symbol="EURUSD", detail="no quote", started_at=utcnow() - timedelta(minutes=2))
    db.add(inc)
    db.commit()
    health.monitor(db)
    db.commit()
    assert not db.scalars(select(Alert).where(Alert.type == "HEALTH")).all()  # still within grace
    inc.started_at = utcnow() - timedelta(minutes=6)
    db.commit()
    health.monitor(db)
    db.commit()
    assert any("prices stopped" in a.title for a in db.scalars(select(Alert).where(Alert.type == "HEALTH")).all())
    assert c.get("/api/health/deep").json()["ok"] is False
    inc.ended_at = utcnow()
    db.commit()
    health.monitor(db)
    db.commit()
    assert any(a.title.startswith("✅ Recovered") for a in db.scalars(select(Alert).where(Alert.type == "HEALTH")).all())
    health.heartbeat(db)
    db.commit()
    assert c.get("/api/health/deep").json() == {"ok": True, "worker_seconds_ago": 0, "problems": 0, "kinds": []}


def test_backup_settings_and_send(c, db):
    assert c.put("/api/backup", json={"offsite_telegram": True}).status_code == 400
    assert c.put("/api/backup", json={"backup_password": "short", "password": PW}).status_code == 400
    r = c.put("/api/backup", json={"backup_password": "my-long-backup-pass", "password": PW, "offsite_telegram": True}).json()
    assert r["has_password"] and r["offsite_telegram"]
    backup_tool.record_local("trading-x.sql.gz", 123)
    got = []
    assert backup_tool.send("trading-x.sql.gz", b"DUMP", http=lambda req: got.append(req) or {"ok": True}) == "sent"
    assert b"QVXB1" in got[0].data and b'filename="trading-x.sql.gz.qvx"' in got[0].data
    assert backup_tool.send("trading-x.sql.gz", b"DUMP", http=lambda req: {"ok": True}).startswith("already sent")
    st = c.get("/api/backup").json()["status"]
    assert st["last_local_name"] == "trading-x.sql.gz" and st["last_offsite_name"].endswith(".qvx")
