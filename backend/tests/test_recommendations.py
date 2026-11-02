"""Review recommendations: NUMERIC migration, per-environment risk profiles, live-gate enforcement,
currency exposure, broker-only live evidence, wider pair list, simulator idempotency."""
import os
from datetime import timedelta

import pytest

if os.getenv("APP_ENV") != "test":
    pytest.skip("integration tests need APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Alert, Position, Quote, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import livegate, trading  # noqa: E402
from app.services import market_data as md  # noqa: E402

PW = "Test#Operator-Pass1"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    bootstrap()
    db = SessionLocal()
    db.add(User(username="op", password_hash=hash_password(PW), is_admin=True, must_change_password=False, totp_enabled=False))
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


def _type(table, col):
    with engine.connect() as cn:
        return cn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),
                          {"t": table, "c": col}).scalar()


def test_money_columns_numeric_and_migration_converts_old_databases(db):
    assert _type("positions", "realized_pnl") == "numeric" and _type("candles", "close") == "numeric"
    assert _type("candles", "tick_volume") == "double precision"  # counts/ratios stay float
    # simulate an install from before the change: float columns and no migration history
    with engine.begin() as cn:
        cn.execute(text('ALTER TABLE positions ALTER COLUMN realized_pnl TYPE double precision'))
        cn.execute(text('ALTER TABLE quotes ALTER COLUMN bid TYPE double precision'))
        cn.execute(text("DELETE FROM alembic_version"))
    init_db()
    assert _type("positions", "realized_pnl") == "numeric" and _type("quotes", "bid") == "numeric"
    init_db()  # idempotent
    md.run_simulator(db, ["EURUSD"], ["H1"])
    md.run_simulator(db, ["EURUSD"], ["H1"])  # twice before commit: no duplicate quote row
    db.commit()
    q = db.get(Quote, "EURUSD")
    assert isinstance(q.bid, float) and q.ask > q.bid  # Python still gets floats


def test_pairs_and_strategies_available(c, db):
    syms = state.get(db, "strategy")["symbols"]
    assert {"EURUSD", "AUDUSD", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP"} <= set(syms)
    assert "pairs_v2" in state.get(db, "migrations") and "risk_profiles_v1" in state.get(db, "migrations")
    names = {s["name"] for s in c.get("/api/system/strategies").json()}
    assert {"london_breakout", "supertrend_ema", "ichimoku_tk"} <= names
    r = c.put("/api/system/strategy", json={"values": {"name": "ichimoku_tk"}})
    assert r.status_code == 200, r.text
    c.put("/api/system/strategy", json={"values": {"name": "trend_pullback"}})


def test_risk_profiles_are_independent_and_live_never_looser(c, db):
    base = c.get("/api/system/risk").json()
    assert base["profile"] == "practice" and set(base["profiles"]) == {"practice", "demo", "live"}
    # practice change does not move demo/live
    r = c.put("/api/system/risk", json={"values": {"risk_per_trade_pct": 0.2}})
    assert r.status_code == 200, r.text
    assert c.get("/api/system/risk?profile=demo").json()["risk"]["risk_per_trade_pct"] == 0.5
    assert state.risk_for(db, "MT5_LIVE")["risk_per_trade_pct"] == 0.5 and state.risk_for(db, "PAPER")["risk_per_trade_pct"] == 0.2
    # live cannot be looser than demo
    r = c.put("/api/system/risk", json={"profile": "live", "values": {"risk_per_trade_pct": 1.0}, "password": PW})
    assert r.status_code == 400 and "looser than demo" in r.json()["detail"]
    r = c.put("/api/system/risk", json={"profile": "live", "values": {"risk_per_trade_pct": 0.3}})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert state.risk_for(db, "CRYPTO_LIVE")["risk_per_trade_pct"] == 0.3
    # demo increase needs the password; lowering demo below live clamps live at read time
    assert c.put("/api/system/risk", json={"profile": "demo", "values": {"risk_per_trade_pct": 1.0}}).status_code in (400, 401, 403)
    assert c.put("/api/system/risk", json={"profile": "demo", "values": {"risk_per_trade_pct": 0.1}}).status_code == 200
    db.expire_all()
    assert state.risk_for(db, "MT5_LIVE")["risk_per_trade_pct"] == 0.1
    # the pilot preset lowers every profile
    assert c.post("/api/system/risk/pilot-preset").status_code == 200
    db.expire_all()
    assert all(state.risk_profile(db, p)["max_trades_per_day"] <= 3 for p in state.RISK_PROFILES)
    assert c.get("/api/system/risk?profile=bogus").status_code == 400


def test_gate_expiry_switches_off_real_money_and_auto_trade(db):
    g = state.get(db, "live_gate")
    state.put(db, "live_gate", {**g, "approved": True, "expires_at": (utcnow() + timedelta(days=1)).isoformat()})
    tr = state.get(db, "trading")
    state.put(db, "trading", {**tr, "mode": "MT5_LIVE"})
    cc = state.get(db, "crypto")
    state.put(db, "crypto", {**cc, "mode": "LIVE"})
    ctl = state.get(db, "controls")
    state.put(db, "controls", {**ctl, "auto_execute": {"PAPER": True, "MT5_LIVE": True, "CRYPTO_LIVE": True}})
    db.commit()
    assert livegate.enforce(db) == []  # approved: nothing changes
    state.put(db, "live_gate", {**g, "approved": True, "expires_at": (utcnow() - timedelta(minutes=1)).isoformat()})
    db.commit()
    done = livegate.enforce(db)
    db.commit()
    db.expire_all()
    assert len(done) == 3
    assert state.get(db, "trading")["mode"] == "MT5_DEMO" and state.get(db, "crypto")["mode"] == "PAPER"
    flags = state.get(db, "controls")["auto_execute"]
    assert flags["PAPER"] and not flags["MT5_LIVE"] and not flags["CRYPTO_LIVE"]
    assert db.scalar(select(Alert).where(Alert.type == "LIVE_GATE_EXPIRED")) is not None
    state.put(db, "trading", {**tr, "mode": "PAPER"})
    state.put(db, "crypto", {**cc, "mode": "OFF"})
    state.put(db, "controls", {**ctl, "auto_execute": {}})
    state.put(db, "live_gate", g)
    db.commit()


def test_live_evidence_counts_only_broker_trades(db):
    now = utcnow()
    for i in range(60):
        db.add(Position(mode="PAPER", symbol="EURUSD", side="BUY", volume=0.01, open_price=1.1, status="CLOSED",
                        opened_at=now - timedelta(days=40, hours=i), closed_at=now - timedelta(days=1, hours=i), realized_pnl=1.0))
    db.commit()
    ev = livegate.evidence(db)["demo_results_satisfactory"]
    assert not ev["passed"] and "0/50" in ev["detail"] and "practice trades don't count: 60" in ev["detail"]
    assert all(r["passed"] for r in livegate.self_test())


def test_currency_exposure_blocks_correlated_trades(db):
    risk = state.get(db, "risk")
    state.put(db, "risk", {**risk, "max_concurrent_positions": 5, "max_same_currency_positions": 2})
    db.commit()
    for s in ("EURUSD", "AUDUSD", "GBPUSD"):
        md.ensure_instrument(db, s)
    for s, side in (("EURUSD", "BUY"), ("AUDUSD", "BUY")):
        db.add(Position(mode="PAPER", symbol=s, side=side, volume=0.01, open_price=1.0, status="OPEN"))
    db.commit()
    ctx = trading.build_context(db, symbol="GBPUSD", side="BUY", tf="H1", sl=1.0, tp=2.0, mode="PAPER")
    from app.services.risk_engine import evaluate
    chk = next(x for x in evaluate(ctx).checks if x["check"].startswith("currency exposure"))
    assert not chk["passed"] and "short USD" in chk["detail"]
    for p in db.scalars(select(Position).where(Position.status == "OPEN")).all():
        p.status = "CLOSED"
    state.put(db, "risk", risk)
    db.commit()
