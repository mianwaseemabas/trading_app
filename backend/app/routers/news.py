from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..db import get_db
from ..deps import require
from ..models import NewsEvent, User, utcnow
from ..services import news as news_svc

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
def list_news(days_back: int = Query(1, ge=0, le=14), days_ahead: int = Query(7, ge=0, le=14), impact: str | None = None,
              currency: str | None = None, user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    now = utcnow()
    q = select(NewsEvent).where(NewsEvent.time >= now - timedelta(days=days_back), NewsEvent.time <= now + timedelta(days=days_ahead))
    if impact:
        q = q.where(NewsEvent.impact.in_(impact.split(",")))
    if currency:
        q = q.where(NewsEvent.currency.in_(currency.upper().split(",")))
    rows = db.scalars(q.order_by(NewsEvent.time).limit(1000)).all()
    controls = state.get(db, "controls")
    blocked_now = {}
    strat = state.get(db, "strategy")
    for sym in strat.get("symbols", []):
        ok, detail = news_svc.news_check(db, sym, controls)
        blocked_now[sym] = {"blocked": not ok, "detail": detail}
    return {"events": [{"time": e.time, "currency": e.currency, "impact": e.impact, "title": e.title, "forecast": e.forecast,
                        "previous": e.previous, "actual": e.actual} for e in rows],
            "status": state.get(db, "news_status"), "filter": {k: controls[k] for k in ("news_filter", "news_before_min", "news_after_min", "news_impacts")},
            "symbols": blocked_now, "server_time": now}


@router.post("/refresh")
def refresh(user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    try:
        news_svc.refresh(db, force=True)
    except Exception as e:
        raise HTTPException(502, f"Calendar fetch failed: {e}")
    return state.get(db, "news_status")


# ------------------------------------------------------------ headlines
@router.get("/headlines")
def list_headlines(hours: int = Query(48, ge=1, le=720), impact: str | None = None, asset: str | None = None,
                   category: str | None = None, mine: bool = False, limit: int = Query(200, le=500),
                   user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    from ..models import NewsHeadline
    from ..services import headlines as hl
    q = select(NewsHeadline).where(NewsHeadline.published >= utcnow() - timedelta(hours=hours))
    if impact:
        q = q.where(NewsHeadline.impact.in_(impact.split(",")))
    if category:
        q = q.where(NewsHeadline.category == category)
    rows = db.scalars(q.order_by(NewsHeadline.published.desc()).limit(1000)).all()
    watched = hl.watched_assets(db)
    if asset:
        rows = [r for r in rows if asset.upper() in r.assets]
    if mine:
        rows = [r for r in rows if set(r.assets) & watched]
    return {"items": [{"id": r.id, "source": r.source, "category": r.category, "title": r.title, "summary": r.summary, "url": r.url,
                       "published": r.published, "impact": r.impact, "tags": r.tags, "assets": r.assets,
                       "yours": bool(set(r.assets) & watched)} for r in rows[:limit]],
            "status": state.get(db, "headline_status"), "watched": sorted(watched),
            "note": "Impact is a keyword rule (shown under each headline), not a prediction. Check the source before acting."}


@router.post("/headlines/refresh")
def refresh_headlines(user: User = Depends(require("system.settings")), db: Session = Depends(get_db)):
    from ..services import headlines as hl
    st = hl.refresh(db, force=True)
    db.commit()
    return st
