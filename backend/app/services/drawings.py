"""Chart line alerts: a horizontal or trend line with the bell on fires once when the price crosses it."""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChartDrawing, Quote, utcnow


def level_now(d: ChartDrawing, now_ts: float | None = None) -> float | None:
    x = d.data or {}
    if d.kind == "hline":
        return float(x["price"]) if x.get("price") is not None else None
    if d.kind == "trend" and all(k in x for k in ("t1", "p1", "t2", "p2")) and x["t2"] != x["t1"]:
        t = now_ts or time.time()
        return float(x["p1"]) + (float(x["p2"]) - float(x["p1"])) * (t - float(x["t1"])) / (float(x["t2"]) - float(x["t1"]))
    return None


def check(db: Session) -> int:
    from .alerts import raise_alert
    fired = 0
    for d in db.scalars(select(ChartDrawing).where(ChartDrawing.alert.is_(True))).all():
        q = db.get(Quote, d.symbol)
        lvl = level_now(d)
        if not q or not q.bid or lvl is None:
            continue
        mid = (q.bid + q.ask) / 2
        side = "above" if mid >= lvl else "below"
        if d.side_last and side != d.side_last:
            what = d.label or ("price line" if d.kind == "hline" else "trend line")
            raise_alert(db, "PRICE_ALERT", f"🔔 {d.symbol} crossed {'above' if side == 'above' else 'below'} {lvl:.5g} ({what})",
                        f"Price now {mid:.6g}. Alert line #{d.id} on your chart — it is now switched off (one-time).",
                        severity="warning", dedup=f"draw:{d.id}:{utcnow().isoformat()}", symbol=d.symbol, link=f"#/terminal?s={d.symbol}")
            d.alert, d.triggered_at = False, utcnow()
            fired += 1
        d.side_last = side
    return fired
