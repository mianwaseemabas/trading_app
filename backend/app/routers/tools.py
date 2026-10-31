"""Chart drawings + line alerts, strategy race, position-size calculator."""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import ChartDrawing, Instrument, Quote, User
from ..services import race
from ..services import market_data as md

router = APIRouter(prefix="/api", tags=["tools"])


def _d(d: ChartDrawing) -> dict:
    from ..services.drawings import level_now
    lv = level_now(d)
    return {"id": d.id, "symbol": d.symbol, "kind": d.kind, "data": d.data, "color": d.color, "label": d.label, "alert": d.alert,
            "triggered_at": d.triggered_at, "level_now": lv}


@router.get("/drawings")
def list_drawings(symbol: str | None = None, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    q = select(ChartDrawing).where(ChartDrawing.user_id == user.id)
    if symbol:
        q = q.where(ChartDrawing.symbol == symbol.upper())
    return [_d(d) for d in db.scalars(q.order_by(ChartDrawing.id)).all()]


class DrawIn(BaseModel):
    symbol: str = Field(max_length=32)
    kind: str
    data: dict
    color: str = Field(default="#f2b443", max_length=16)
    label: str = Field(default="", max_length=60)
    alert: bool = False


def _validate(kind: str, data: dict) -> dict:
    try:
        if kind == "hline":
            p = float(data["price"])
            assert p > 0 and math.isfinite(p)
            return {"price": p}
        if kind == "trend":
            out = {k: float(data[k]) for k in ("t1", "p1", "t2", "p2")}
            assert out["t1"] != out["t2"] and out["p1"] > 0 and out["p2"] > 0
            return out
    except (KeyError, TypeError, ValueError, AssertionError):
        pass
    raise HTTPException(400, "Invalid line")


@router.post("/drawings")
def add_drawing(body: DrawIn, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    if body.kind not in ("hline", "trend"):
        raise HTTPException(400, "kind must be hline or trend")
    if db.query(ChartDrawing).filter(ChartDrawing.user_id == user.id).count() >= 300:
        raise HTTPException(400, "Too many lines — delete some first")
    d = ChartDrawing(user_id=user.id, symbol=body.symbol.upper(), kind=body.kind, data=_validate(body.kind, body.data),
                     color=body.color, label=body.label, alert=body.alert)
    db.add(d)
    db.commit()
    return _d(d)


class DrawPatch(BaseModel):
    data: dict | None = None
    label: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=16)
    alert: bool | None = None


@router.put("/drawings/{did}")
def edit_drawing(did: int, body: DrawPatch, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    d = db.get(ChartDrawing, did)
    if not d or d.user_id != user.id:
        raise HTTPException(404, "Line not found")
    if body.data is not None:
        d.data, d.side_last = _validate(d.kind, body.data), ""
    for k in ("label", "color"):
        if getattr(body, k) is not None:
            setattr(d, k, getattr(body, k))
    if body.alert is not None:
        d.alert, d.side_last, d.triggered_at = body.alert, "", None if body.alert else d.triggered_at
    db.commit()
    return _d(d)


@router.delete("/drawings/{did}")
def delete_drawing(did: int, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    d = db.get(ChartDrawing, did)
    if not d or d.user_id != user.id:
        raise HTTPException(404, "Line not found")
    db.delete(d)
    db.commit()
    return {"status": "ok"}


# ------------------------------------------------------------------ strategy race
@router.get("/race")
def get_race(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    return race.leaderboard(db)


class RaceIn(BaseModel):
    timeframes: list[str] = ["H1"]


@router.post("/race/reset")
def reset_race(body: RaceIn, request: Request, user: User = Depends(require("strategy.edit")), db: Session = Depends(get_db)):
    if not body.timeframes or not set(body.timeframes) <= {"M15", "H1", "H4"}:
        raise HTTPException(400, "timeframes must be among M15, H1, H4")
    race.reset(db, body.timeframes)
    audit(db, "race.reset", user=user, ip=client_ip(request), details={"timeframes": body.timeframes})
    db.commit()
    return race.leaderboard(db)


# ------------------------------------------------------------------ calculator
class SizeIn(BaseModel):
    symbol: str = Field(max_length=32)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)
    risk_amount: float = Field(gt=0, le=1_000_000)


@router.post("/tools/size")
def size(body: SizeIn, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    from ..services import trading
    sym = body.symbol.upper()
    inst = db.get(Instrument, sym) or md.ensure_instrument(db, sym)
    if not inst:
        raise HTTPException(404, "Unknown market")
    dist = abs(body.entry - body.stop)
    if dist <= 0:
        raise HTTPException(400, "Stop must differ from entry")
    acct_ccy = "USDT" if sym.endswith(("USDT", "USDC")) else "USD"
    quotes = {q.symbol: q for q in db.scalars(select(Quote)).all()}
    vpu = trading.value_per_unit(db, inst, acct_ccy if acct_ccy == "USD" else inst.currency_profit, quotes) or inst.contract_size
    raw = body.risk_amount / (dist * vpu)
    step = inst.volume_step or 0.01
    vol = math.floor(raw / step + 1e-9) * step
    pip = 0.01 if sym.endswith("JPY") else 0.0001 if len(sym) == 6 and sym.isalpha() else None
    out = {"symbol": sym, "units_label": "lots" if (inst.contract_size or 1) > 1 else "coins/units", "size_exact": round(raw, 6),
           "size": round(vol, 8), "min_size": inst.volume_min, "risk_at_size": round(vol * dist * vpu, 2),
           "value_per_price_unit": vpu, "stop_distance": dist, "stop_pips": round(dist / pip, 1) if pip else None,
           "stop_pct": round(dist / body.entry * 100, 3), "notional": round(vol * (inst.contract_size or 1) * body.entry, 2)}
    if body.target:
        out["reward"] = round(vol * abs(body.target - body.entry) * vpu, 2)
        out["rr"] = round(abs(body.target - body.entry) / dist, 2)
        out["break_even_win_rate_pct"] = round(100 / (1 + out["rr"]), 1)
    if vol < (inst.volume_min or 0):
        out["warning"] = f"Your risk is too small for the minimum size {inst.volume_min}: that would risk {inst.volume_min * dist * vpu:.2f}."
    return out


# ------------------------------------------------------------------ opportunity ranking
_scan_at: dict = {}


@router.get("/opportunities")
def get_opps(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    from ..services import opportunities as op
    st = state.get(db, "opportunities")
    return {**{k: v for k, v in st.items() if k != "ts"}, "config": op.cfg(db),
            "note": "Score = quality of evidence, not a chance of winning. Money figures use the risk engine's size for your account "
                    "and include spread, commission and a slippage allowance."}


@router.post("/opportunities/scan")
def scan_opps(user: User = Depends(require("signals.view")), db: Session = Depends(get_db)):
    import time as _t

    from ..services import opportunities as op
    if _t.time() - _scan_at.get(user.id, 0) < 30:
        raise HTTPException(429, "A scan just ran — wait 30 seconds")
    _scan_at[user.id] = _t.time()
    op.scan(db, force=True)
    db.commit()
    return get_opps(user, db)


@router.post("/opportunities/{key}/queue")
def queue_opp(key: str, request: Request, user: User = Depends(require("trading.manual")), db: Session = Depends(get_db)):
    from ..services import opportunities as op
    res = op.queue(db, key, by=user.username)
    audit(db, "opportunity.queued", user=user, ip=client_ip(request), details={"key": key, **{k: v for k, v in res.items() if k != "detail"}})
    db.commit()
    return res


class OppCfgIn(BaseModel):
    auto_proven: bool


@router.put("/opportunities/config")
def opp_cfg(body: OppCfgIn, request: Request, user: User = Depends(require("trading.control")), db: Session = Depends(get_db)):
    c = state.get_locked(db, "opportunity_cfg")
    c["auto_proven"] = body.auto_proven
    state.put(db, "opportunity_cfg", c, by=user.username)
    audit(db, "opportunity.config", user=user, ip=client_ip(request), details=c)
    db.commit()
    return get_opps(user, db)


class ExplainIn(BaseModel):
    key: str = Field(max_length=20)


@router.post("/opportunities/explain")
def explain_opp(body: ExplainIn, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    """AI signal analyst: supporting, missing and conflicting evidence for one ranked setup (never a trade instruction)."""
    import json as _json
    import time as _t

    from ..secretbox import decrypt
    from .ai import _call
    item = next((x for x in state.get(db, "opportunities").get("items", []) if x["key"] == body.key), None)
    if not item:
        raise HTTPException(404, "Opportunity not found — scan again")
    ai = state.get(db, "ai")
    if not ai.get("enabled") or not ai.get("api_key_enc"):
        raise HTTPException(400, "Turn on the AI assistant first (AI Assistant page)")
    if _t.time() - _scan_at.get(("x", user.id), 0) < 5:
        raise HTTPException(429, "Please wait a few seconds")
    _scan_at[("x", user.id)] = _t.time()
    prompt = ("You are the signal analyst in the Qivynex trading app. Review this ranked setup for a beginner using exactly these headings: "
              "SUPPORTING EVIDENCE, MISSING EVIDENCE, CONFLICTING EVIDENCE, COSTS & RISK (use the money numbers), WHAT WOULD MAKE IT INVALID. "
              "Then one line 'Bottom line:' that says whether the evidence is strong, mixed or weak — never tell the user to buy or sell, "
              "never predict price, remind that score is not a win probability. Max 220 words.\n\nSETUP (JSON):\n" + _json.dumps(item, default=str)[:9000])
    text = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), "You explain trading evidence honestly and briefly.", [{"role": "user", "content": prompt}])
    return {"key": body.key, "analysis": text}
