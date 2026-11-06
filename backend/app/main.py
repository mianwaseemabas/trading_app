from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from .config import settings
from .db import init_db, session_scope
from .routers import academy, ai, assist, easy, insight, push, tadawul, tools, auth, backtests, bridge, brokers, crypto, intel, lab, live, market, news, setups, system, trades, trust, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

settings.validate()

@asynccontextmanager
async def lifespan(_app):
    bootstrap()
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.is_dev else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.is_dev else None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


for r in (auth, users, system, trust, lab, academy, push, assist, insight, tools, tadawul, easy, setups, crypto, intel, market, trades, backtests, bridge, brokers, live, ai, news):
    app.include_router(r.router)
app.include_router(live.pos_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/health/deep")
def health_deep():
    """For an external uptime check: worker alive, no long-running data/exchange/backup problems. No secrets."""
    from .db import SessionLocal
    from .services.health import deep
    db = SessionLocal()
    try:
        return deep(db)
    finally:
        db.close()


# Development convenience only: in production the bundled nginx serves the dashboard.
if settings.is_dev and os.getenv("SERVE_STATIC_DIR"):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=os.getenv("SERVE_STATIC_DIR"), html=True), name="static")


def _migrate(db, state) -> None:
    """One-time settings upgrades for installs created before a feature existed."""
    done = state.get(db, "migrations") or []
    if "alert_types_v2" not in done:
        p = state.get(db, "alert_prefs")
        ch = p.setdefault("channels", {})
        for c in ("telegram", "push"):
            cur = ch.setdefault(c, [])
            for t in (["AI_BRIEF", "AI_COACH", "HEALTH", "PRICE_ALERT"] + (["SIGNAL_QUALIFIED", "AUTO_TRADE", "ORDER_FILLED", "STOP_HIT",
                                                                              "TARGET_1_HIT", "KILL_SWITCH_ACTIVE"] if c == "push" else [])):
                if t not in cur:
                    cur.append(t)
        state.put(db, "alert_prefs", p)
        done = done + ["alert_types_v2"]
        state.put(db, "migrations", done)
    if "risk_profiles_v1" not in done:
        # Demo and live get their own copy of today's limits, so later practice changes no longer move them.
        practice = state.get(db, "risk")
        profs = state.get(db, "risk_profiles")
        for name in ("demo", "live"):
            if not profs.get(name):
                profs[name] = dict(practice)
        state.put(db, "risk_profiles", profs)
        done = done + ["risk_profiles_v1"]
        state.put(db, "migrations", done)
    if "pairs_v2" not in done:
        # Widen the watched forex pairs (keeps any the owner added) and the liquid crypto list.
        s = state.get(db, "strategy")
        s["symbols"] = (s["symbols"] + [x for x in state.DEFAULTS["strategy"]["symbols"] if x not in s["symbols"]])[:20]
        state.put(db, "strategy", s)
        cc = state.get(db, "crypto")
        cc["symbols"] = (cc.get("symbols", []) + [x for x in state.DEFAULTS["crypto"]["symbols"] if x not in cc.get("symbols", [])])[:20]
        state.put(db, "crypto", cc)
        done = done + ["pairs_v2"]
        state.put(db, "migrations", done)


def bootstrap() -> None:
    """Create tables, default settings and (first run only) the initial admin."""
    from . import state
    from .audit import audit
    from .models import User
    from .security import hash_password, password_problems

    init_db()
    with session_scope() as db:
        # Serialise bootstrap across uvicorn workers and the engine worker.
        db.execute(text("SELECT pg_advisory_xact_lock(724003)"))
        state.ensure_defaults(db)
        db.flush()
        _migrate(db, state)
        if db.scalar(select(User.id).limit(1)) is None:
            u, p = settings.initial_admin_username.strip().lower(), settings.initial_admin_password
            if u and p:
                problems = password_problems(p, u)
                if problems:
                    log.error("INITIAL_ADMIN_PASSWORD too weak (%s); admin not created", ", ".join(problems))
                else:
                    db.add(User(username=u, full_name="Administrator", password_hash=hash_password(p),
                                is_admin=True, must_change_password=True, created_by="bootstrap"))
                    audit(db, "user.bootstrap_admin", details={"username": u})
                    log.warning("Initial admin '%s' created. Change the password at first login.", u)
            else:
                log.warning("No users exist. Run: python -m app.cli create-admin <username>")

