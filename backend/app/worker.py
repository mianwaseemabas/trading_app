"""Background trading engine. Exactly one instance runs (PostgreSQL advisory lock)."""
from __future__ import annotations

import logging
import signal
import time

from sqlalchemy import text

from .config import settings
from .db import SessionLocal, engine
from .main import bootstrap
from .services.engine import housekeeping, tick

log = logging.getLogger("worker")
_running = True


def _stop(*_):
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    bootstrap()
    lock_conn = engine.connect()
    while _running and not lock_conn.execute(text("SELECT pg_try_advisory_lock(724002)")).scalar():
        log.warning("Another worker holds the engine lock; waiting...")
        time.sleep(10)
    log.info("Trading engine started (interval %ss)", settings.worker_interval_s)
    from .services.tgbot import start_thread
    start_thread()
    last_housekeeping = 0.0
    while _running:
        started = time.time()
        db = SessionLocal()
        try:
            tick(db)
            if started - last_housekeeping > 3600:
                housekeeping(db)
                last_housekeeping = started
        except Exception:
            db.rollback()
            log.exception("Engine tick failed")
        finally:
            db.close()
        time.sleep(max(1.0, settings.worker_interval_s - (time.time() - started)))
    lock_conn.close()
    log.info("Trading engine stopped")


if __name__ == "__main__":
    main()
