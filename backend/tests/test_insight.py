"""Time Machine odds, strategy-vs-hold comparison and the AI bull/bear review (veto/shrink only)."""
import os
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from app.services import ai_review as air
from app.services import benchmark as bm
from app.services import strategy as st
from app.services import timemachine as tm
from app.state import DEFAULTS


def synth(n=3000, seed=3, drift=0.0):
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(drift, 0.0012, n)) + 0.02 * np.sin(np.arange(n) / 90)
    opn = np.r_[close[0], close[:-1]]
    hi = np.maximum(opn, close) + rng.uniform(0, 0.0008, n)
    lo = np.minimum(opn, close) - rng.uniform(0, 0.0008, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": opn, "high": hi, "low": lo, "close": close, "tick_volume": 100, "spread_points": 10}, index=idx)


# ------------------------------------------------------------------ Time Machine
def test_break_even_and_wilson():
    assert tm.break_even_pct(2.0) == pytest.approx(33.3, abs=0.1)
    assert tm.break_even_pct(1.0, cost_r=0.1) == pytest.approx(55.0)
    assert tm.break_even_pct(0) is None
    lo, hi = tm.wilson(50, 100)
    assert lo < 50 < hi and tm.wilson(0, 0) is None


def test_resolve_is_conservative_when_both_hit_in_one_candle():
    hi, lo, cl = np.array([1.2, 1.0]), np.array([0.8, 1.0]), np.array([1.0, 1.0])
    assert tm.resolve(hi, lo, cl, 0, "BUY", sl=0.9, tp=1.1)[0] == "STOP"
    assert tm.resolve(hi, lo, cl, 0, "SELL", sl=1.1, tp=0.9)[0] == "STOP"
    assert tm.resolve(np.array([1.05]), np.array([0.95]), np.array([1.0]), 0, "BUY", 0.9, 1.1)[0] == "OPEN"


def test_simulation_matches_random_walk_theory():
    # driftless random walk: P(+2 ATR before −1 ATR) ≈ 1/3
    df = synth(3000, seed=11)
    df["close"] = 1.1 + np.cumsum(np.random.default_rng(5).normal(0, 0.001, len(df)))
    df["open"] = np.r_[df["close"].iloc[0], df["close"].iloc[:-1]]
    df["high"] = np.maximum(df["open"], df["close"]) + 0.0002
    df["low"] = np.minimum(df["open"], df["close"]) - 0.0002
    e = float(df["close"].iloc[-1])
    sim = tm.simulate(df, "BUY", e, e - 0.002, e + 0.004, paths=4000, max_bars=600)
    assert sim["paths"] == 4000 and 24 <= sim["p_target_pct"] <= 42, sim
    again = tm.simulate(df, "BUY", e, e - 0.002, e + 0.004, paths=4000, max_bars=600)
    assert again["p_target_pct"] == sim["p_target_pct"]  # reproducible (fixed seed)
    sell = tm.simulate(df, "SELL", e, e + 0.002, e - 0.004, paths=4000, max_bars=600)
    assert 20 <= sell["p_target_pct"] <= 45
    assert tm.simulate(df.head(50), "BUY", e, e - 0.002, e + 0.004)["paths"] == 0


@pytest.mark.parametrize("name", ["trend_pullback", "donchian_breakout", "london_breakout"])
def test_replay_never_uses_the_future(name):
    df = synth()
    cfg = {**DEFAULTS["strategy"], "name": name}
    ind = st.prepare(df, cfg)
    cut = 2400
    a = tm.replay(ind, cfg, "BUY", until=cut)
    # appending different future candles must not change the replay up to `cut`
    alt = pd.concat([df.iloc[:cut], synth(len(df), seed=99).iloc[cut:].set_axis(df.index[cut:])])
    b = tm.replay(st.prepare(alt, cfg), cfg, "BUY", until=cut)
    assert (a["n"], a["target_first"], a["stop_first"]) == (b["n"], b["target_first"], b["stop_first"])
    assert a["n"] == a["target_first"] + a["stop_first"] + a["expired"]
    assert all(pd.Timestamp(x["time"]) < df.index[cut] for x in a["recent"])


def test_analyze_report_shape_and_verdicts():
    df = synth()
    e = float(df["close"].iloc[-1])
    r = tm.analyze(df, {**DEFAULTS["strategy"], "name": "trend_pullback"}, "BUY", e, e - 0.003, e + 0.006, cost_r=0.05, paths=2000)
    assert r["reward_risk"] == 2.0 and r["break_even_pct"] == pytest.approx(35.0)
    assert r["replay"]["n"] > 0 and r["simulation"]["paths"] == 2000
    assert r["verdict"]["code"] in ("ABOVE_BREAK_EVEN", "UNCLEAR", "BELOW_BREAK_EVEN", "TOO_FEW") and r["honesty"]
    manual = tm.analyze(df, None, "SELL", e, e + 0.003, e - 0.003, paths=1000)
    assert manual["replay"] is None and manual["verdict"]["code"] == "TOO_FEW"
    rep = {"n": 50, "p_target_pct": 20.0, "p_target_ci95": [10.0, 30.0], "break_even_pct": 34.0, "avg_planned_rr": 2.0}
    assert tm.verdict(rep, {"paths": 1, "p_target_pct": 33.0}, 34.0, 2.0)["code"] == "BELOW_BREAK_EVEN"
    rep2 = {**rep, "p_target_pct": 50.0, "p_target_ci95": [40.0, 60.0]}
    v = tm.verdict(rep2, {"paths": 1, "p_target_pct": 33.0}, 34.0, 2.0)
    assert v["code"] == "ABOVE_BREAK_EVEN" and v["edge_pts"] == 17.0
    assert tm.verdict(rep2, {"paths": 1, "p_target_pct": 33.0}, 34.0, 4.0)["edge_pts"] is None  # different trade shape


# ------------------------------------------------------------------ strategy vs hold
def test_buy_and_hold_and_versus():
    df = synth(1000, drift=0.0002)
    h = bm.buy_and_hold(df, start=100, initial=1000, spread=0.0001, fee_pct=0.1)
    raw = (df["close"].iloc[-1] - df["open"].iloc[100]) / df["open"].iloc[100] * 100
    assert h["available"] and h["return_pct"] < raw and h["max_drawdown_pct"] >= 0 and len(h["curve"]) <= 302
    good = bm.versus({"trades": 40, "return_pct": h["return_pct"] + 5, "max_drawdown_pct": 1.0}, h)
    assert good["code"] == "BEATS_HOLD" and good["beats_return"] and good["beats_risk_adjusted"]
    bad = bm.versus({"trades": 40, "return_pct": -3, "max_drawdown_pct": 6}, h)
    assert bad["code"] == "LOSES_TO_HOLD"
    assert bm.versus({"trades": 0, "return_pct": 0, "max_drawdown_pct": 0}, h)["code"] == "NO_TRADES"
    assert bm.versus({"trades": 5}, {"available": False})["code"] == "NO_BENCHMARK"
    assert bm.max_drawdown_pct([100, 120, 90, 130]) == 25.0
    assert len(bm.downsample([{"t": i} for i in range(1000)], 100)) == 101


# ------------------------------------------------------------------ AI review (pure policy)
def test_stance_and_parse():
    assert air.stance_of("BUY", "BUY") == "AGREE" and air.stance_of("Overweight", "SELL") == "OPPOSE"
    assert air.stance_of("SELL", "SELL") == "AGREE" and air.stance_of("HOLD", "BUY") == "NEUTRAL" and air.stance_of("??", "BUY") == "ERROR"
    v = air.parse_verdict('Sure! ```json\n{"rating": "underweight", "confidence": "72.4", "bull_point": "trend up", '
                          '"bear_point": "CPI in 1h", "change_mind": "close above 1.09", "summary": "mixed",}\n```')
    assert v["rating"] == "UNDERWEIGHT" and v["confidence"] == 72 and v["bear_point"] == "CPI in 1h"
    for bad in ("", "no json here", '{"rating": "STRONG BUY"}'):
        with pytest.raises(ValueError):
            air.parse_verdict(bad)
    assert air.parse_verdict('{"rating": "BUY", "confidence": 500}')["confidence"] == 100


def test_decide_fails_closed_and_never_enlarges():
    c = {"min_confidence": 60, "neutral_action": "HALF"}
    assert air.decide(None, "BUY", c)[0] == "VETO"
    assert air.decide({"error": "timeout"}, "BUY", c)[0] == "VETO"
    assert air.decide({"rating": "BUY", "stance": "AGREE", "confidence": 10}, "BUY", c)[0] == "ALLOW"
    assert air.decide({"rating": "SELL", "stance": "OPPOSE", "confidence": 80}, "BUY", c)[0] == "VETO"
    assert air.decide({"rating": "SELL", "stance": "OPPOSE", "confidence": 40}, "BUY", c)[0] == "HALF"
    assert air.decide({"rating": "HOLD", "stance": "NEUTRAL", "confidence": 50}, "BUY", {**c, "neutral_action": "ALLOW"})[0] == "ALLOW"
    assert air.decide({"rating": "X", "stance": "ERROR"}, "BUY", c)[0] == "VETO"
    assert air.shrink(0.05, 0.01, 0.01) == 0.02          # rounded down to the step
    assert air.shrink(0.01, 0.01, 0.01) is None           # below the broker minimum
    assert air.shrink(0.0, 0.01, 0.01) is None
    for v in (0.02, 0.37, 1.0, 3.33):
        s = air.shrink(v, 0.01, 0.01)
        assert s is None or s <= v / 2 + 1e-9


# ------------------------------------------------------------------ integration
if os.getenv("APP_ENV") != "test":
    pytest.skip("integration part needs APP_ENV=test", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app import state  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import AIReview, Candle, Order, Quote, SignalApproval, SignalEvent, User, utcnow  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import engine as eng  # noqa: E402
from app.services import market_data as md  # noqa: E402
from app.services import trading  # noqa: E402
from app.services.strategy import StrategyResult  # noqa: E402

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
    df = synth(900)
    for t, r in df.iterrows():
        db.merge(Candle(symbol="EURUSD", timeframe="H4", time=t.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close,
                        tick_volume=1, spread_points=0, source="simulator", collected_at=utcnow()))  # other sources are purged
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


def test_settings_loosening_needs_password(c):
    g = c.get("/api/insight/settings").json()
    assert g["ai_review"]["mode"] == "OFF" and g["time_machine"]["enabled"] and not g["ai_ready"]
    assert c.put("/api/insight/settings", json={"mode": "VETO"}).status_code == 400  # AI not set up
    assert c.put("/api/insight/settings", json={"tm_gate": True}).json()["time_machine"]["gate"] is True  # tighter: no password
    assert c.put("/api/insight/settings", json={"tm_gate": False}).status_code == 403                       # looser: password
    assert c.put("/api/insight/settings", json={"tm_gate": False, "password": PW}).status_code == 200
    assert c.put("/api/insight/settings", json={"neutral_action": "VETO"}).status_code == 200
    assert c.put("/api/insight/settings", json={"neutral_action": "ALLOW"}).status_code == 403
    assert c.put("/api/insight/settings", json={"neutral_action": "HALF", "password": PW}).status_code == 200
    assert c.put("/api/insight/settings", json={"mode": "NOPE"}).status_code == 400


def test_manual_time_machine_api(c, db):
    q = db.get(Quote, "EURUSD")
    body = {"symbol": "EURUSD", "timeframe": "H4", "side": "BUY", "entry": q.ask, "stop_loss": q.ask - 0.004, "take_profit": q.ask + 0.008,
            "strategy": "trend_pullback"}
    r = c.post("/api/insight/time-machine", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"]["code"] and out["break_even_pct"] and out["simulation"]["paths"] > 0 and out["replay"]["n"] >= 0
    assert c.post("/api/insight/time-machine", json={**body, "stop_loss": q.ask + 0.001}).status_code == 400


def test_compare_vs_hold_api(c):
    r = c.post("/api/lab/compare", json={"symbol": "EURUSD", "timeframe": "H4", "strategies": ["trend_pullback", "donchian_breakout"]})
    assert r.status_code == 200, r.text
    for _ in range(120):
        g = c.get("/api/lab/compare").json()
        if g["status"] in ("DONE", "FAILED"):
            break
        time.sleep(0.5)
    assert g["status"] == "DONE", g
    assert len(g["results"]) == 2 and g["hold"]["available"] and g["hold_curve"] and g["summary"]
    assert all(x["vs_hold"]["code"] for x in g["results"]) and all("curve" in x for x in g["results"])
    assert c.post("/api/lab/compare", json={"symbol": "XXXYYY", "timeframe": "H4", "strategies": ["trend_pullback"]}).status_code == 400
    assert c.post("/api/lab/compare", json={"symbol": "EURUSD", "timeframe": "H4", "strategies": ["nope"]}).status_code == 400


def _prep_auto(db, monkeypatch, answer):
    """Qualified BUY setup on EURUSD H1, auto-trade on for PAPER, AI review in VETO mode with a fake AI answer."""
    q = db.get(Quote, "EURUSD")
    monkeypatch.setattr(eng.strat, "evaluate", lambda df, i, cfg: StrategyResult(
        "BUY", q.bid, round(q.bid - 0.0012, 5), round(q.bid + 0.0025, 5), {"atr": 0.001}, [{"check": "test", "passed": True}]))
    last = md.load_candles(db, "EURUSD", "H1", limit=5).index[-1].to_pydatetime()
    from app.services import assess
    monkeypatch.setattr(eng, "utcnow", lambda: last + timedelta(minutes=61))
    monkeypatch.setattr(assess, "utcnow", lambda: last + timedelta(minutes=61))
    monkeypatch.setattr(air, "ai_ready", lambda db: True)
    monkeypatch.setattr("app.secretbox.decrypt", lambda v: "test-key")
    calls = []

    def fake_call(provider, model, key, system, messages, timeout=60, max_tokens=900):
        calls.append(system[:20])
        if "RISK MANAGER" in system:
            if isinstance(answer, Exception):
                raise answer
            return answer
        return "- point one\n- point two"
    monkeypatch.setattr("app.routers.ai._call", fake_call)
    a = state.get(db, "assessment")
    a.update(min_watch=0, min_candidate=0)
    state.put(db, "assessment", a)
    rk = state.get(db, "risk")
    rk["max_trades_per_day"] = 50
    state.put(db, "risk", rk)
    ctl = state.get(db, "controls")
    ctl.update(auto_execute={"PAPER": True}, auto_min_score=0, require_approval=False)
    state.put(db, "controls", ctl)
    state.put(db, "news_status", {"ok": True, "last_fetch": utcnow().isoformat(), "error": "", "count": 0})
    r = state.get(db, "ai_review")
    r.update(mode="VETO", style="debate", neutral_action="HALF", min_confidence=60)
    state.put(db, "ai_review", r)
    db.commit()
    cfg = {**state.get(db, "strategy"), "version": f"ai{time.time_ns()}"}
    return cfg, calls


def test_ai_veto_sends_auto_trade_to_approval(c, db, monkeypatch):
    cfg, calls = _prep_auto(db, monkeypatch, '{"rating": "SELL", "confidence": 85, "bull_point": "a", "bear_point": "b", '
                                             '"change_mind": "c", "summary": "against"}')
    sig = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    rv = db.get(AIReview, sig.id)
    assert sig.status == "AWAITING" and db.get(SignalApproval, sig.id).status == "WAITING", sig.status
    assert rv.stance == "OPPOSE" and rv.action == "VETO" and rv.calls == 3 and len(calls) == 3
    assert not db.scalars(select(Order).where(Order.signal_id == sig.id)).all()
    ins = c.get(f"/api/insight/{sig.id}").json()
    assert ins["ai_review"]["rating"] == "SELL" and ins["ai_mode"] == "VETO"


def test_ai_error_fails_closed(c, db, monkeypatch):
    cfg, _ = _prep_auto(db, monkeypatch, RuntimeError("provider down"))
    sig = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    rv = db.get(AIReview, sig.id)
    assert sig.status == "AWAITING" and rv.stance == "ERROR" and rv.action == "VETO" and "provider down" in rv.error


def test_ai_agree_places_and_neutral_halves(c, db, monkeypatch):
    cfg, _ = _prep_auto(db, monkeypatch, '{"rating": "BUY", "confidence": 70, "summary": "ok"}')
    sig = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    assert sig.status == "ORDERED" and db.get(AIReview, sig.id).action == "ALLOW"
    full = db.scalars(select(Order).where(Order.signal_id == sig.id)).one().volume
    for p in c.get("/api/positions?status=OPEN").json():
        c.post(f"/api/positions/{p['id']}/close")

    cfg, _ = _prep_auto(db, monkeypatch, '{"rating": "HOLD", "confidence": 50, "summary": "mixed"}')
    sig2 = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    rv = db.get(AIReview, sig2.id)
    evs = {e.state: e.detail for e in db.scalars(select(SignalEvent).where(SignalEvent.signal_id == sig2.id)).all()}
    if air.shrink(full, 0.01, 0.01) is None:  # half of the size is below the broker minimum → manual approval
        assert sig2.status == "AWAITING" and rv.action == "VETO" and "AI_VETO" in evs
    else:
        o = db.scalars(select(Order).where(Order.signal_id == sig2.id)).one()
        assert sig2.status == "ORDERED" and rv.action == "HALF" and "AI_HALF_SIZE" in evs and o.volume <= full / 2 + 1e-9


def test_log_only_never_touches_the_trade_and_stats(c, db, monkeypatch):
    cfg, calls = _prep_auto(db, monkeypatch, '{"rating": "SELL", "confidence": 99}')
    r = state.get(db, "ai_review")
    r["mode"] = "LOG_ONLY"
    state.put(db, "ai_review", r)
    db.commit()
    for p in c.get("/api/positions?status=OPEN").json():
        c.post(f"/api/positions/{p['id']}/close")
    sig = eng.evaluate_symbol(db, "EURUSD", "H1", cfg, "PAPER")
    db.commit()
    assert sig.status == "ORDERED" and db.get(AIReview, sig.id) is None and not calls  # placed without waiting for the AI
    st_ = c.get("/api/insight/stats/ai").json()
    assert st_["code"] == "TOO_EARLY" and "by_stance" in st_
    r["mode"] = "OFF"
    state.put(db, "ai_review", r)
    ctl = state.get(db, "controls")
    ctl.update(auto_execute={}, require_approval=True)
    state.put(db, "controls", ctl)
    db.commit()
