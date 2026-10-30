from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db, session_scope
from ..deps import client_ip, require
from ..models import AuditLog, BacktestRun, User, utcnow
from ..services import market_data as md
from ..services.backtest import run_backtest
from ..services.trading import spec_of

router = APIRouter(prefix="/api", tags=["backtest"])
log = logging.getLogger("backtest")


class BacktestIn(BaseModel):
    symbol: str = Field(max_length=32)
    timeframe: str = "H1"
    start: date | None = None
    end: date | None = None
    initial_balance: float = Field(500, ge=10, le=10_000_000)
    spread_points: float = Field(12, ge=0, le=500)
    slippage_points: float = Field(3, ge=0, le=500)
    commission_per_lot: float = Field(7, ge=0, le=100)
    swap_per_lot_per_night: float = Field(-1.0, ge=-100, le=100)
    leverage: int = Field(100, ge=1, le=1000)
    oos_fraction: float = Field(0.3, ge=0.1, le=0.6)
    folds: int = Field(4, ge=2, le=12)
    strategy_overrides: dict = {}


def _execute(run_id: int) -> None:
    with session_scope() as db:
        run = db.get(BacktestRun, run_id)
        p = run.params
        try:
            start = datetime.combine(date.fromisoformat(p["start"]), time.min, tzinfo=timezone.utc) if p.get("start") else None
            end = datetime.combine(date.fromisoformat(p["end"]), time.max, tzinfo=timezone.utc) if p.get("end") else None
            if start is None:
                df = md.load_candles(db, p["symbol"], p["timeframe"], limit=200_000)
                if end is not None:
                    df = df[df.index <= end]
            else:
                df = md.load_candles(db, p["symbol"], p["timeframe"], start=start, end=end)
            if len(df) < 500:
                raise ValueError(f"Only {len(df)} candles available; need at least 500. Import history first.")
            inst = md.ensure_instrument(db, p["symbol"])
            spec = spec_of(inst)
            cfg = {**state.get(db, "strategy"), **p.get("strategy_overrides", {})}
            risk = state.get(db, "risk")
            res = run_backtest(df, cfg, risk, spec, p)
            run.metrics = {**res["metrics"], "strategy": {k: cfg[k] for k in cfg if k not in ("symbols", "timeframes")}}
            run.trades = res["trades"]
            run.equity_curve = res["equity_curve"]
            run.folds = res["folds"]
            run.status = "DONE"
        except Exception as e:
            log.exception("backtest %s failed", run_id)
            run.status, run.error = "FAILED", str(e)[:2000]
        run.finished_at = utcnow()


@router.post("/backtests")
def create_backtest(body: BacktestIn, request: Request, tasks: BackgroundTasks,
                    user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    if body.timeframe not in md.TF_SECONDS:
        raise HTTPException(400, "Unknown timeframe")
    running = db.scalar(select(BacktestRun).where(BacktestRun.status == "RUNNING", BacktestRun.created_by == user.username))
    if running:
        raise HTTPException(409, "You already have a backtest running")
    params = body.model_dump(mode="json")
    params["symbol"] = body.symbol.strip().upper()
    run = BacktestRun(created_by=user.username, params=params)
    db.add(run)
    db.flush()
    audit(db, "backtest.started", user=user, ip=client_ip(request), entity="backtest", entity_id=run.id, details=params)
    db.commit()
    tasks.add_task(_execute, run.id)
    return {"id": run.id, "status": run.status}


@router.get("/backtests")
def list_backtests(user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    rows = db.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(100)).all()
    return [{"id": r.id, "created_at": r.created_at, "created_by": r.created_by, "params": r.params,
             "status": r.status, "error": r.error, "finished_at": r.finished_at,
             "summary": (r.metrics or {}).get("all"), "robust": (r.metrics or {}).get("robust")} for r in rows]


@router.get("/backtests/{run_id}")
def get_backtest(run_id: int, user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    r = db.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, "Not found")
    return {"id": r.id, "created_at": r.created_at, "created_by": r.created_by, "params": r.params, "status": r.status,
            "error": r.error, "metrics": r.metrics, "trades": r.trades, "equity_curve": r.equity_curve, "folds": r.folds}


@router.get("/audit")
def audit_log(
    action: str | None = None,
    username: str | None = None,
    limit: int = Query(300, le=2000),
    user: User = Depends(require("audit.view")),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if username:
        q = q.where(AuditLog.username == username)
    return [{"id": a.id, "at": a.at, "username": a.username, "action": a.action, "entity": a.entity,
             "entity_id": a.entity_id, "details": a.details, "ip": a.ip} for a in db.scalars(q).all()]
