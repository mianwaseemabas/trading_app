"""Saudi stocks (Tadawul): watch-only list management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import state
from ..db import get_db
from ..deps import require
from ..models import User
from ..services import tadawul

router = APIRouter(prefix="/api/tadawul", tags=["tadawul"])


@router.get("")
def get_list(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    st = state.get(db, "tadawul")
    return {"symbols": st.get("symbols", []), "status": st.get("status", {}), "popular": tadawul.POPULAR, "market_open": tadawul.market_open(),
            "note": "Watch and research only — prices from Yahoo Finance (public, may be delayed ~15 min). Placing Saudi stock trades "
                    "needs a CMA-licensed broker with an API; the app never sends orders for these symbols."}


class ListIn(BaseModel):
    symbols: list[str] = Field(max_length=15)


@router.put("")
def put_list(body: ListIn, user: User = Depends(require("strategy.edit")), db: Session = Depends(get_db)):
    syms = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    bad = [s for s in syms if not tadawul.VALID.match(s)]
    if bad:
        raise HTTPException(400, f"Use Tadawul codes like 2222.SR (or ^TASI.SR for the index): {', '.join(bad)}")
    st = state.get_locked(db, "tadawul")
    st["symbols"] = syms
    state.put(db, "tadawul", st, by=user.username)
    db.commit()
    return get_list(user, db)


@router.post("/refresh")
def refresh(user: User = Depends(require("market.view")), db: Session = Depends(get_db)):
    st = tadawul.refresh(db, force=True)
    db.commit()
    return st
