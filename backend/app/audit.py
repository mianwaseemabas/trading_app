from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, EventLog


def audit(
    db: Session,
    action: str,
    *,
    user=None,
    entity: str = "",
    entity_id: Any = "",
    details: dict | None = None,
    ip: str = "",
) -> None:
    db.add(
        AuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None) or (user if isinstance(user, str) else "system"),
            action=action,
            entity=entity,
            entity_id=str(entity_id or ""),
            details=details or {},
            ip=ip or "",
        )
    )


def event(db: Session, source: str, message: str, level: str = "INFO", details: dict | None = None) -> None:
    db.add(EventLog(source=source, message=message, level=level, details=details or {}))
