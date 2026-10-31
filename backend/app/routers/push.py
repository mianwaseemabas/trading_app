"""Phone / browser push notification registration."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require
from ..models import PushSubscription, User
from ..services import webpush

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/key")
def key(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    _, public = webpush.vapid_keys(db)
    db.commit()
    n = len(db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id)).all())
    return {"public_key": public, "devices": n}


class SubIn(BaseModel):
    endpoint: str = Field(max_length=800)
    keys: dict
    device: str = Field(default="", max_length=120)


@router.post("/subscribe")
def subscribe(body: SubIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    if not body.endpoint.startswith("https://") or not body.keys.get("p256dh") or not body.keys.get("auth"):
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid subscription")
    s = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if s is None:
        s = PushSubscription(endpoint=body.endpoint)
        db.add(s)
    s.user_id, s.p256dh, s.auth, s.device, s.fails = user.id, body.keys["p256dh"][:200], body.keys["auth"][:100], body.device, 0
    db.commit()
    return {"status": "ok"}


class UnsubIn(BaseModel):
    endpoint: str = Field(max_length=800)


@router.post("/unsubscribe")
def unsubscribe(body: UnsubIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    s = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id))
    if s:
        db.delete(s)
        db.commit()
    return {"status": "ok"}
