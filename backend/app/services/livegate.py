"""Live-trading gate: manual checklist + automatically verified evidence + expiry.

Real-money modes (BROKER LIVE, CRYPTO LIVE) are usable only while `is_approved()` is true.
The check runs on the server for every order context and for the connector config, so a
frontend toggle can never enable live trading on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import state
from ..models import BacktestRun, DataIncident, Position, User, utcnow

APPROVAL_DAYS = 30
MIN_TRADES = 50
MIN_DAYS = 28


def is_approved(db: Session) -> bool:
    g = state.get(db, "live_gate")
    if not g.get("approved"):
        return False
    exp = g.get("expires_at")
    return bool(exp) and datetime.fromisoformat(exp) > utcnow()


def self_test() -> list[dict]:
    """Deterministic failure tests of the risk engine (no I/O). All must pass."""
    from .risk_engine import AccountState, InstrumentSpec, OpenPos, RiskContext, evaluate
    from ..state import DEFAULTS
    spec = InstrumentSpec("EURUSD", 0.00001, 0.00001, None, 100000, 0.01, 100, 0.01, "EUR", "USD")

    def ctx(**kw):
        base = dict(side="BUY", symbol="EURUSD", bid=1.08, ask=1.0801, quote_age_s=1, stop_loss=1.078, take_profit=1.084, spec=spec,
                    account=AccountState(500, 500, 500, "USD", 100), cfg=dict(DEFAULTS["risk"]), mode="PAPER",
                    day_anchor_equity=500, week_anchor_equity=500, profit_to_account_rate=1.0)
        base.update(kw)
        return RiskContext(**base)

    cases = [
        ("valid trade is accepted", ctx(), True),
        ("missing stop-loss is rejected", ctx(stop_loss=None), False),
        ("stop on the wrong side is rejected", ctx(stop_loss=1.09), False),
        ("kill switch blocks orders", ctx(kill_switch=True), False),
        ("loss-limit lock blocks orders", ctx(locked=True, lock_reason="test"), False),
        ("daily loss limit blocks orders", ctx(day_loss=20), False),
        ("stale quote is rejected", ctx(quote_age_s=10_000), False),
        ("stale candles are rejected", ctx(data_ok=False, data_detail="test"), False),
        ("wide spread is rejected", ctx(ask=1.0830), False),
        ("trade too small for the broker minimum is rejected", ctx(stop_loss=1.03), False),
        ("duplicate order is rejected", ctx(duplicate=True), False),
        ("max open positions is enforced", ctx(open_positions=[OpenPos("GBPUSD", "BUY")]), False),
        ("averaging down is rejected", ctx(open_positions=[OpenPos("EURUSD", "BUY", -5)], cfg={**DEFAULTS["risk"], "max_concurrent_positions": 3,
                                                                                             "max_positions_per_symbol": 2}), False),
        ("daily trade count is enforced", ctx(trades_today=99), False),
        ("unknown mode cannot trade", ctx(mode="OFF"), False),
    ]
    out = []
    for name, c, expect in cases:
        d = evaluate(c)
        ok = d.approved == expect and (not d.approved or d.risk_amount <= d.risk_budget + 1e-9)
        out.append({"test": name, "passed": ok, "expected": "accept" if expect else "reject", "got": "accept" if d.approved else "reject"})
    return out


def evidence(db: Session, user: User | None = None) -> dict:
    g = state.get(db, "live_gate")
    strat = state.get(db, "strategy")
    items = {}
    bt = db.scalars(select(BacktestRun).where(BacktestRun.status == "DONE").order_by(BacktestRun.id.desc()).limit(30)).all()
    good = None
    for b in bt:
        m = b.metrics or {}
        ft = (m.get("segments") or {}).get("final_test") or {}
        if (m.get("strategy") or {}).get("name") == strat["name"] and ft.get("trades", 0) >= 30 and (ft.get("profit_factor") or 0) >= 1.1 \
                and (ft.get("net_pnl") or 0) > 0:
            good = b
            break
    items["strategy_oos_documented"] = {
        "passed": good is not None,
        "detail": f"backtest #{good.id}: final-test {good.metrics['segments']['final_test']['trades']} trades, PF {good.metrics['segments']['final_test']['profit_factor']}"
        if good else f"need a backtest of '{strat['name']}' whose final 20% has ≥30 trades, profit factor ≥1.1 and positive net"}
    since = utcnow() - timedelta(days=120)
    rows = db.scalars(select(Position).where(Position.mode.in_(["PAPER", "MT5_DEMO"]), Position.status == "CLOSED",
                                             Position.closed_at >= since, Position.realized_pnl.is_not(None))).all()
    n = len(rows)
    span = (max(p.closed_at for p in rows) - min(p.opened_at for p in rows)).days if rows else 0
    net = sum(p.realized_pnl for p in rows)
    rec_open = db.scalar(select(func.count()).select_from(DataIncident).where(DataIncident.kind == "RECONCILIATION", DataIncident.ended_at.is_(None))) or 0
    items["demo_results_satisfactory"] = {
        "passed": n >= MIN_TRADES and span >= MIN_DAYS and net > 0 and rec_open == 0,
        "detail": f"{n}/{MIN_TRADES} closed practice/demo trades over {span}/{MIN_DAYS} days, net {net:+.2f}, open reconciliation incidents {rec_open}"}
    st = g.get("self_test") or {}
    fresh = bool(st.get("at")) and datetime.fromisoformat(st["at"]) > utcnow() - timedelta(days=7)
    items["risk_failure_tests_passed"] = {"passed": bool(st.get("passed")) and fresh,
                                          "detail": f"self-test {'passed' if st.get('passed') else 'failed or not run'} {st.get('at', '')[:16]}"
                                                    + ("" if fresh else " — run it (valid 7 days)")}
    mfa = bool(user and user.totp_enabled)
    items["credentials_secured"] = {"passed": mfa and bool(g.get("notes", {}).get("credentials_secured")),
                                    "detail": ("2FA on" if mfa else "turn on 2FA for your account") + " + describe key restrictions (IP, no withdrawals)"}
    from ..models import ExamResult
    ex = db.scalar(select(ExamResult).where(ExamResult.passed.is_(True), *( [ExamResult.user_id == user.id] if user else [] )).limit(1))
    items["academy_exam_passed"] = {"passed": ex is not None,
                                    "detail": f"readiness exam passed with {ex.score}%" if ex else "pass the Academy readiness exam (Academy page)"}
    items["broker_legal_checked"] = {"passed": bool(g.get("notes", {}).get("broker_legal_checked")),
                                     "detail": "write what you checked (broker licence, Saudi CMA guidance, tax) — manual"}
    return items
