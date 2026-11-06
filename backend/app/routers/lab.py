"""Strategy lab API: rank strategies with costs, AI review of the ranking, apply a choice."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import User
from ..secretbox import decrypt
from ..services import lab
from ..services.strategies_extra import INFO
from ..services.strategy import STRATEGIES

router = APIRouter(prefix="/api/lab", tags=["lab"])

BASE_INFO = {
    "trend_pullback": {"label": "EMA trend pullback (baseline)", "family": "trend", "typical_win_rate": "35–50%", "payoff": "target 2R",
                       "rules": "With EMA 50 above EMA 200 and price above EMA 200, BUY when the MACD histogram turns positive and RSI is 50–70. "
                                "Stop 1.5×ATR, target 2×stop.", "best_when": "steady trends", "weak_when": "ranges"},
    "smc_sweep_bos": {"label": "SMC sweep → BOS → retest", "family": "price action", "typical_win_rate": "35–50% (few signals)",
                      "payoff": "target 2R", "rules": "Liquidity sweep, then a structure break with a displacement candle, then a retest "
                      "of the fair value gap / order block. Stop beyond the sweep.", "best_when": "liquid sessions with clear swings",
                      "weak_when": "low-volume drift"},
}


@router.get("")
def get_lab(user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    st = state.get(db, "strategy_lab")
    return {**st, "running": lab._lock.locked(), "catalog": [{"name": k, "description": v, **(INFO.get(k) or BASE_INFO.get(k) or {})}
                                                             for k, v in STRATEGIES.items()],
            "markets": list(state.get(db, "strategy")["symbols"]) + list(state.get(db, "crypto").get("symbols", [])),
            "live": {"forex": state.get(db, "strategy")["name"], "crypto": state.get(db, "crypto").get("strategy") or state.get(db, "strategy")["name"]},
            "honesty": "A high win rate alone does not make money: mean-reversion wins often but small; trend systems win less but bigger. "
                       "Judge by net expectancy after costs on the untouched final 20% of the data, with enough trades."}


class RunIn(BaseModel):
    timeframes: list[str] = ["H1"]
    backfill_days: int = Field(default=0, ge=0, le=730)


@router.post("/run")
def run_lab(body: RunIn, request: Request, user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    if not body.timeframes or not set(body.timeframes) <= {"M15", "H1", "H4", "D1"}:
        raise HTTPException(400, "timeframes must be among M15, H1, H4, D1")
    if not lab.start(body.model_dump()):
        raise HTTPException(409, "The strategy lab is already running")
    audit(db, "lab.run", user=user, ip=client_ip(request), details=body.model_dump())
    db.commit()
    return {"status": "started"}


class CompareIn(BaseModel):
    symbol: str = Field(max_length=32)
    timeframe: str = "H1"
    strategies: list[str] = Field(min_length=1, max_length=lab.MAX_COMPARE)
    candles: int = Field(default=8000, ge=500, le=25_000)
    fee_pct: float | None = Field(default=None, ge=0, le=1)  # crypto only: exchange fee per side, %


@router.get("/compare")
def get_compare(user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    return {**state.get(db, "strategy_compare"), "running": lab._cmp_lock.locked(), "max": lab.MAX_COMPARE}


@router.post("/compare")
def run_compare(body: CompareIn, request: Request, user: User = Depends(require("backtest.run")), db: Session = Depends(get_db)):
    if body.timeframe not in {"M15", "H1", "H4", "D1"}:
        raise HTTPException(400, "timeframe must be M15, H1, H4 or D1")
    bad = [s for s in body.strategies if s not in STRATEGIES]
    if bad:
        raise HTTPException(400, f"Unknown strategy: {', '.join(bad)}")
    watched = set(state.get(db, "strategy")["symbols"]) | set(state.get(db, "crypto").get("symbols", []))
    if body.symbol not in watched:
        raise HTTPException(400, "Pick one of your watched markets")
    params = {**body.model_dump(), "strategies": list(dict.fromkeys(body.strategies))}
    if not lab.start_compare(params):
        raise HTTPException(409, "A comparison is already running")
    audit(db, "lab.compare", user=user, ip=client_ip(request), details=params)
    db.commit()
    return {"status": "started"}


class ApplyIn(BaseModel):
    target: str  # forex | crypto
    strategy: str


@router.post("/apply")
def apply(body: ApplyIn, request: Request, user: User = Depends(require("strategy.edit")), db: Session = Depends(get_db)):
    if body.strategy not in STRATEGIES:
        raise HTTPException(400, "Unknown strategy")
    if body.target == "crypto":
        c = state.get_locked(db, "crypto")
        old, c["strategy"] = c.get("strategy"), body.strategy
        state.put(db, "crypto", c, by=user.username)
    elif body.target == "forex":
        from .system import _bump
        s = state.get_locked(db, "strategy")
        old = s["name"]
        if old != body.strategy:
            s["name"], s["version"] = body.strategy, _bump(s["version"])
        state.put(db, "strategy", s, by=user.username)
    else:
        raise HTTPException(400, "target must be forex or crypto")
    audit(db, "lab.apply", user=user, ip=client_ip(request), details={"target": body.target, "from": old, "to": body.strategy})
    db.commit()
    return {"status": "ok", "target": body.target, "strategy": body.strategy}


@router.post("/ai-review")
def ai_review(request: Request, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    from .ai import SYSTEM_PROMPT, _call
    ai = state.get(db, "ai")
    if not ai["enabled"] or not ai.get("api_key_enc"):
        raise HTTPException(400, "Turn on the AI assistant first (AI Assistant page)")
    st = state.get(db, "strategy_lab")
    if st.get("status") != "DONE":
        raise HTTPException(400, "Run the strategy lab first")
    data = {"results": st["results"][:25], "catalog": {k: INFO.get(k) or BASE_INFO.get(k) for k in STRATEGIES}}
    prompt = ("You are reviewing backtest rankings of trading strategies on the user's markets (costs included; 'final_test' is the "
              "untouched last 20% of the data). Recommend which strategy to PAPER-TEST on which market and timeframe, and which to avoid. "
              "Use sections: OBSERVED (numbers from the data), INTERPRETATION, UNCERTAINTY (sample size, overfitting, regime change), "
              "RECOMMENDATION (max 3 lines; paper test only, never real money; say 'none' if nothing shows an edge). "
              "Never promise profit or describe a win rate as a probability of future profit.\n\nDATA:\n" + json.dumps(data, default=str)[:14000])
    text = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), SYSTEM_PROMPT, [{"role": "user", "content": prompt}])
    audit(db, "lab.ai_review", user=user, ip=client_ip(request))
    db.commit()
    return {"review": text}
