from __future__ import annotations

import hmac
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User, UserSession, utcnow
from .security import token_hash, user_has

SESSION_COOKIE = "qt_session"
CSRF_COOKIE = "qt_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    # The backend is only reachable through the bundled nginx, which sets
    # X-Real-IP after resolving the real client address from trusted proxies.
    ip = request.headers.get("x-real-ip")
    if ip:
        return ip.strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _load_session(request: Request, db: Session, allow_pending_2fa: bool = False) -> tuple[UserSession, User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    now = utcnow()
    if (
        sess is None
        or sess.revoked
        or sess.expires_at <= now
        or sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now
    ):
        raise HTTPException(401, "Session expired")
    if sess.pending_2fa and not allow_pending_2fa:
        raise HTTPException(401, "Two-factor verification required")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Account disabled")
    # CSRF: mutating requests must echo the per-session token in a header.
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token", "")
        if not header or not hmac.compare_digest(header, sess.csrf_token or ""):
            raise HTTPException(403, "CSRF token missing or invalid")
    # Sliding idle timeout (write at most once a minute).
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    request.state.session = sess
    return sess, user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    _, user = _load_session(request, db)
    request.state.user = user
    return user


def current_user_any(request: Request, db: Session = Depends(get_db)) -> User:
    """Also allows users that still must change their password."""
    _, user = _load_session(request, db)
    return user


def active_user(user: User = Depends(current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(403, "Password change required")
    if user.require_2fa and not user.totp_enabled:
        raise HTTPException(403, "Two-factor setup required")
    return user


def require(perm: str):
    def dep(user: User = Depends(active_user)) -> User:
        if not user_has(user, perm):
            raise HTTPException(403, f"Missing permission: {perm}")
        return user

    return dep
