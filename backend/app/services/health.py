"""Health monitor: tells you (Telegram / phone) when something has been broken for 5+ minutes, and when it recovers.

Watches: open-market price feeds (data incidents), the crypto exchange connection, the broker connector,
the worker itself (heartbeat, checked by /api/health/deep and the optional GitHub uptime check) and backups.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..models import DataIncident, utcnow

GRACE = timedelta(minutes=5)


def heartbeat(db: Session) -> None:
    hb = state.get(db, "health")
    hb["worker_at"] = utcnow().isoformat()
    state.put(db, "health", hb)


def _age(iso: str | None) -> float | None:
    if not iso:
        return None
    return (utcnow() - datetime.fromisoformat(iso)).total_seconds()


def problems(db: Session) -> dict[str, str]:
    """Current problems that are already older than the grace period: key -> description."""
    out: dict[str, str] = {}
    now = utcnow()
    for inc in db.scalars(select(DataIncident).where(DataIncident.ended_at.is_(None))).all():
        if now - inc.started_at >= GRACE and inc.kind in ("DATA_STALE", "BROKER_DISCONNECTED"):
            what = "Broker connection lost" if inc.kind == "BROKER_DISCONNECTED" else f"{inc.symbol}: prices stopped"
            out[f"inc:{inc.key}"] = f"{what} for {int((now - inc.started_at).total_seconds() // 60)} min ({inc.detail[:120]})"
    cc = state.get(db, "crypto")
    if cc.get("mode", "OFF") != "OFF":
        cs = state.get(db, "crypto_status")
        err_age = _age(cs.get("market_error_since")) if cs.get("market_error") else None
        if err_age is not None and err_age >= GRACE.total_seconds():
            out["exchange"] = f"{cc.get('exchange')} connection failing for {int(err_age // 60)} min: {str(cs.get('market_error'))[:150]}"
    bk = state.get(db, "backup_status")
    age = _age(bk.get("last_local_at"))
    if bk.get("expected") and (age is None or age > 36 * 3600):
        out["backup"] = "No database backup in the last 36 hours — check the server cron (deploy/backup.sh)."
    return out


def monitor(db: Session) -> None:
    from .alerts import raise_alert
    h = state.get(db, "health")
    active = h.get("active", {})
    now_p = problems(db)
    for k, desc in now_p.items():
        if k not in active:
            active[k] = utcnow().isoformat()
            raise_alert(db, "HEALTH", f"⚠️ {desc.split(' for ')[0]}", desc, severity="critical", dedup=f"health:{k}:{active[k]}", link="#/dashboard")
    for k in list(active):
        if k not in now_p:
            mins = int((_age(active.pop(k)) or 0) // 60)
            raise_alert(db, "HEALTH", f"✅ Recovered: {k.split(':')[-1]}", f"Back to normal after about {mins} min.",
                        dedup=f"health-ok:{k}:{utcnow().isoformat()}", link="#/dashboard")
    h["active"] = active
    state.put(db, "health", h)


def deep(db: Session) -> dict:
    """Public, secret-free health summary for an external uptime check."""
    w = _age(state.get(db, "health").get("worker_at"))
    p = problems(db)
    return {"ok": (w is not None and w < 300) and not p, "worker_seconds_ago": None if w is None else int(w),
            "problems": len(p), "kinds": sorted({k.split(":")[0] for k in p})}
