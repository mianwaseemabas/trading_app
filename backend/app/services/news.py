"""Economic calendar (scheduled high-impact news) and the news trading filter.

Source: the public ForexFactory calendar JSON feed published by Fair Economy
(nfs.faireconomy.media). Fields: title, country (currency code), date (ISO with
offset), impact (High/Medium/Low/Holiday), forecast, previous. The free feed has
no "actual" values. If the feed is unavailable the filter reports itself as
unavailable instead of silently blocking or allowing (plan §5D).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import state
from ..audit import event
from ..models import NewsEvent, utcnow

log = logging.getLogger("news")
FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
FETCH_EVERY = timedelta(minutes=60)


def _get(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (QivynexTrade calendar)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def parse(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        try:
            t = datetime.fromisoformat(str(it["date"]).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            out.append({"time": t.astimezone(timezone.utc), "currency": str(it.get("country", "")).upper()[:8],
                        "impact": str(it.get("impact", "")).capitalize()[:12], "title": str(it.get("title", ""))[:200],
                        "forecast": str(it.get("forecast") or "")[:40], "previous": str(it.get("previous") or "")[:40],
                        "actual": str(it.get("actual") or "")[:40]})
        except (KeyError, ValueError):
            continue
    return out


def store(db: Session, rows: list[dict]) -> int:
    now = utcnow()
    for r in rows:
        stmt = pg_insert(NewsEvent).values(**r, fetched_at=now)
        stmt = stmt.on_conflict_do_update(constraint="uq_news_event",
                                          set_={k: stmt.excluded[k] for k in ("impact", "forecast", "previous", "actual", "fetched_at")})
        db.execute(stmt)
    return len(rows)


def refresh(db: Session, force: bool = False) -> None:
    st = state.get(db, "news_status")
    last = datetime.fromisoformat(st["last_fetch"]) if st.get("last_fetch") else None
    if not force and last and utcnow() - last < FETCH_EVERY:
        return
    rows, errors = [], []
    for url in FEEDS:
        try:
            rows += parse(_get(url))
        except Exception as e:  # next week's feed is often not published yet
            errors.append(f"{url.rsplit('/', 1)[-1]}: {e}")
    if rows:
        n = store(db, rows)
        st.update(ok=True, last_fetch=utcnow().isoformat(), error="; ".join(errors)[:300], count=n)
    else:
        st.update(ok=False, last_fetch=utcnow().isoformat(), error="; ".join(errors)[:300] or "empty feed")
        event(db, "news", "Economic calendar unavailable: " + st["error"], "WARNING")
    state.put(db, "news_status", st)
    db.commit()


def blocking_events(db: Session, currencies: list[str], controls: dict, at: datetime | None = None) -> list[NewsEvent]:
    at = at or utcnow()
    before, after = timedelta(minutes=int(controls["news_before_min"])), timedelta(minutes=int(controls["news_after_min"]))
    return db.scalars(
        select(NewsEvent).where(NewsEvent.currency.in_(currencies), NewsEvent.impact.in_(controls["news_impacts"]),
                                NewsEvent.time >= at - after, NewsEvent.time <= at + before).order_by(NewsEvent.time)
    ).all()


def news_check(db: Session, symbol: str, controls: dict) -> tuple[bool, str]:
    """(passed, detail) for the risk engine."""
    if not controls.get("news_filter"):
        return True, "news filter off"
    st = state.get(db, "news_status")
    fresh = st.get("ok") and st.get("last_fetch") and utcnow() - datetime.fromisoformat(st["last_fetch"]) < timedelta(hours=26)
    if not fresh:
        return True, "calendar unavailable — filter inactive"
    from .instruments_meta import news_currencies
    ccys = news_currencies(symbol)
    evs = blocking_events(db, ccys, controls)
    if evs:
        e = evs[0]
        return False, f"{e.impact} news: {e.currency} {e.title} at {e.time.strftime('%H:%M')} UTC"
    return True, "no high-impact news nearby"
