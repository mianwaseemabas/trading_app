from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import SESSION_COOKIE, _load_session, client_ip, current_user_any
from ..models import LoginAttempt, User, UserSession, utcnow
from ..security import (
    DUMMY_HASH,
    effective_permissions,
    hash_password,
    new_token,
    new_totp_secret,
    password_problems,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class TwoFADisableIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(min_length=6, max_length=8)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_max_hours * 3600,
        path="/",
    )


def user_payload(user: User, sess: UserSession | None = None) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "is_admin": user.is_admin,
        "permissions": effective_permissions(user),
        "must_change_password": user.must_change_password,
        "totp_enabled": user.totp_enabled,
        "require_2fa": user.require_2fa,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "csrf_token": sess.csrf_token if sess else None,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    now = utcnow()
    username = body.username.strip().lower()

    recent_ip_failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.at > now - timedelta(minutes=15))
    )
    if recent_ip_failures >= settings.ip_max_failed_per_15min:
        db.add(LoginAttempt(username=username, ip=ip, success=False, reason="ip_rate_limited"))
        db.commit()
        raise HTTPException(429, "Too many failed attempts. Try again later.")

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(body.password, DUMMY_HASH)  # constant-ish timing
        db.add(LoginAttempt(username=username, ip=ip, success=False, reason="unknown_user"))
        db.commit()
        raise HTTPException(401, "Invalid username or password")

    if user.locked_until and user.locked_until > now:
        db.add(LoginAttempt(username=username, ip=ip, success=False, reason="locked"))
        db.commit()
        raise HTTPException(423, "Account temporarily locked. Try again later or contact the administrator.")

    if not verify_password(body.password, user.password_hash) or not user.is_active:
        user.failed_attempts += 1
        reason = "bad_password" if user.is_active else "disabled"
        if user.failed_attempts >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_attempts = 0
            audit(db, "user.locked", user=user, entity="user", entity_id=user.id, ip=ip,
                  details={"minutes": settings.lockout_minutes})
        db.add(LoginAttempt(username=username, ip=ip, success=False, reason=reason))
        db.commit()
        raise HTTPException(401, "Invalid username or password")

    user.failed_attempts = 0
    user.locked_until = None
    token = new_token()
    sess = UserSession(
        token_hash=token_hash(token),
        csrf_token=new_token(),
        user_id=user.id,
        expires_at=now + timedelta(hours=settings.session_max_hours),
        ip=ip,
        user_agent=(request.headers.get("user-agent") or "")[:255],
        pending_2fa=user.totp_enabled,
    )
    db.add(sess)
    if not user.totp_enabled:
        user.last_login_at = now
        user.last_login_ip = ip
        db.add(LoginAttempt(username=username, ip=ip, success=True))
        audit(db, "auth.login", user=user, ip=ip)
    db.commit()
    _set_session_cookie(response, token)
    if user.totp_enabled:
        return {"status": "2fa_required", "csrf_token": sess.csrf_token}
    return {"status": "ok", "user": user_payload(user, sess)}


@router.post("/2fa/verify")
def verify_2fa(body: CodeIn, request: Request, db: Session = Depends(get_db)):
    sess, user = _load_session(request, db, allow_pending_2fa=True)
    ip = client_ip(request)
    if not sess.pending_2fa:
        return {"status": "ok", "user": user_payload(user, sess)}
    if not user.totp_secret or not verify_totp(user.totp_secret, body.code):
        db.add(LoginAttempt(username=user.username, ip=ip, success=False, reason="bad_2fa"))
        user.failed_attempts += 1
        if user.failed_attempts >= settings.max_failed_logins:
            user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
            user.failed_attempts = 0
            sess.revoked = True
            audit(db, "user.locked", user=user, entity="user", entity_id=user.id, ip=ip, details={"via": "2fa"})
        db.commit()
        raise HTTPException(401, "Invalid verification code")
    sess.pending_2fa = False
    # Rotate CSRF token after privilege change.
    sess.csrf_token = new_token()
    user.failed_attempts = 0
    user.last_login_at = utcnow()
    user.last_login_ip = ip
    db.add(LoginAttempt(username=user.username, ip=ip, success=True))
    audit(db, "auth.login", user=user, ip=ip, details={"2fa": True})
    db.commit()
    return {"status": "ok", "user": user_payload(user, sess)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        sess, user = _load_session(request, db, allow_pending_2fa=True)
        sess.revoked = True
        audit(db, "auth.logout", user=user, ip=client_ip(request))
        db.commit()
    except HTTPException:
        pass
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/me")
def me(request: Request, user: User = Depends(current_user_any)):
    return user_payload(user, request.state.session)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn, request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    problems = password_problems(body.new_password, user.username)
    if problems:
        raise HTTPException(400, "Password needs: " + ", ".join(problems))
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(400, "New password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    current = request.state.session
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.id != current.id)
        .values(revoked=True)
    )
    audit(db, "auth.password_changed", user=user, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "user": user_payload(user, current)}


@router.post("/2fa/setup")
def setup_2fa(request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(400, "Two-factor authentication is already enabled")
    user.totp_secret = new_totp_secret()
    db.commit()
    return {"secret": user.totp_secret, "otpauth_uri": totp_uri(user.totp_secret, user.username, settings.app_name)}


@router.post("/2fa/enable")
def enable_2fa(body: CodeIn, request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    if not user.totp_secret or not verify_totp(user.totp_secret, body.code):
        raise HTTPException(400, "Invalid verification code")
    user.totp_enabled = True
    audit(db, "auth.2fa_enabled", user=user, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "user": user_payload(user, request.state.session)}


@router.post("/2fa/disable")
def disable_2fa(
    body: TwoFADisableIn, request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)
):
    if user.require_2fa:
        raise HTTPException(400, "Your administrator requires two-factor authentication")
    if not verify_password(body.password, user.password_hash) or not (
        user.totp_secret and verify_totp(user.totp_secret, body.code)
    ):
        raise HTTPException(400, "Password or code is incorrect")
    user.totp_enabled = False
    user.totp_secret = None
    audit(db, "auth.2fa_disabled", user=user, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "user": user_payload(user, request.state.session)}


@router.get("/sessions")
def my_sessions(request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked.is_(False), UserSession.expires_at > utcnow())
        .order_by(UserSession.created_at.desc())
    ).all()
    cur = request.state.session.id
    return [
        {
            "id": s.id,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "ip": s.ip,
            "user_agent": s.user_agent,
            "current": s.id == cur,
        }
        for s in rows
    ]


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: int, request: Request, user: User = Depends(current_user_any), db: Session = Depends(get_db)):
    s = db.get(UserSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(404, "Session not found")
    s.revoked = True
    audit(db, "auth.session_revoked", user=user, ip=client_ip(request), entity="session", entity_id=session_id)
    db.commit()
    return {"status": "ok"}
