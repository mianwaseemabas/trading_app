from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import User, UserSession, utcnow
from ..security import PERMISSIONS, ROLE_PRESETS, effective_permissions, hash_password, password_problems, user_has

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_CHARS = set(string.ascii_lowercase + string.digits + "._-")


def _temp_password() -> str:
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "O0oIl1")
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(14)) + secrets.choice("!@#$%*?") + secrets.choice("23456789")
        if not password_problems(pw):
            return pw


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    full_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=256)
    is_admin: bool = False
    permissions: list[str] = []
    require_2fa: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    is_admin: bool | None = None
    is_active: bool | None = None
    permissions: list[str] | None = None
    require_2fa: bool | None = None


class ResetPasswordIn(BaseModel):
    password: str | None = Field(default=None, max_length=256)


def _out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "email": u.email,
        "is_admin": u.is_admin,
        "is_active": u.is_active,
        "permissions": u.permissions or [],
        "effective_permissions": effective_permissions(u),
        "totp_enabled": u.totp_enabled,
        "require_2fa": u.require_2fa,
        "must_change_password": u.must_change_password,
        "locked": bool(u.locked_until and u.locked_until > utcnow()),
        "locked_until": u.locked_until,
        "last_login_at": u.last_login_at,
        "last_login_ip": u.last_login_ip,
        "created_at": u.created_at,
        "created_by": u.created_by,
    }


def _check_grant(actor: User, perms: list[str], make_admin: bool | None) -> list[str]:
    unknown = [p for p in perms if p not in PERMISSIONS]
    if unknown:
        raise HTTPException(400, f"Unknown permissions: {', '.join(unknown)}")
    if make_admin and not actor.is_admin:
        raise HTTPException(403, "Only administrators can grant administrator rights")
    if not actor.is_admin:
        missing = [p for p in perms if not user_has(actor, p)]
        if missing:
            raise HTTPException(403, f"You cannot grant permissions you do not hold: {', '.join(missing)}")
    return sorted(set(perms))


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True)))


@router.get("/meta")
def meta(_: User = Depends(require("users.manage"))):
    return {"permissions": PERMISSIONS, "roles": ROLE_PRESETS}


@router.get("")
def list_users(_: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [_out(u) for u in db.scalars(select(User).order_by(User.username)).all()]


@router.post("")
def create_user(
    body: UserCreate, request: Request, actor: User = Depends(require("users.manage")), db: Session = Depends(get_db)
):
    username = body.username.strip().lower()
    if not set(username) <= USERNAME_CHARS:
        raise HTTPException(400, "Username may contain only a-z, 0-9, dot, dash and underscore")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Username already exists")
    perms = _check_grant(actor, body.permissions, body.is_admin)
    generated = body.password is None or body.password == ""
    password = _temp_password() if generated else body.password
    problems = password_problems(password, username)
    if problems:
        raise HTTPException(400, "Password needs: " + ", ".join(problems))
    u = User(
        username=username,
        full_name=body.full_name.strip(),
        email=body.email.strip(),
        password_hash=hash_password(password),
        is_admin=body.is_admin,
        permissions=perms,
        require_2fa=body.require_2fa,
        must_change_password=True,
        created_by=actor.username,
    )
    db.add(u)
    db.flush()
    audit(db, "user.created", user=actor, entity="user", entity_id=u.id, ip=client_ip(request),
          details={"username": username, "is_admin": body.is_admin, "permissions": perms})
    db.commit()
    return {"user": _out(u), "temporary_password": password if generated else None}


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    actor: User = Depends(require("users.manage")),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.is_admin and not actor.is_admin:
        raise HTTPException(403, "Only administrators can modify administrator accounts")
    changes: dict = {}
    if body.permissions is not None:
        u.permissions = _check_grant(actor, body.permissions, None)
        changes["permissions"] = u.permissions
    if body.is_admin is not None and body.is_admin != u.is_admin:
        _check_grant(actor, [], body.is_admin or None)
        if not actor.is_admin:
            raise HTTPException(403, "Only administrators can change administrator rights")
        if u.id == actor.id and not body.is_admin:
            raise HTTPException(400, "You cannot remove your own administrator rights")
        if u.is_admin and not body.is_admin and _active_admins(db) <= 1:
            raise HTTPException(400, "At least one active administrator is required")
        u.is_admin = body.is_admin
        changes["is_admin"] = body.is_admin
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == actor.id:
            raise HTTPException(400, "You cannot disable your own account")
        if u.is_admin and not body.is_active and _active_admins(db) <= 1:
            raise HTTPException(400, "At least one active administrator is required")
        u.is_active = body.is_active
        changes["is_active"] = body.is_active
        if not body.is_active:
            db.execute(update(UserSession).where(UserSession.user_id == u.id).values(revoked=True))
    if body.require_2fa is not None:
        u.require_2fa = body.require_2fa
        changes["require_2fa"] = body.require_2fa
    if body.full_name is not None:
        u.full_name = body.full_name.strip()
        changes["full_name"] = u.full_name
    if body.email is not None:
        u.email = body.email.strip()
        changes["email"] = u.email
    audit(db, "user.updated", user=actor, entity="user", entity_id=u.id, ip=client_ip(request), details=changes)
    db.commit()
    return _out(u)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: ResetPasswordIn,
    request: Request,
    actor: User = Depends(require("users.manage")),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.is_admin and not actor.is_admin:
        raise HTTPException(403, "Only administrators can reset administrator passwords")
    generated = not body.password
    password = _temp_password() if generated else body.password
    problems = password_problems(password, u.username)
    if problems:
        raise HTTPException(400, "Password needs: " + ", ".join(problems))
    u.password_hash = hash_password(password)
    u.must_change_password = True
    u.failed_attempts = 0
    u.locked_until = None
    db.execute(update(UserSession).where(UserSession.user_id == u.id).values(revoked=True))
    audit(db, "user.password_reset", user=actor, entity="user", entity_id=u.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "temporary_password": password if generated else None}


@router.post("/{user_id}/unlock")
def unlock(user_id: int, request: Request, actor: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    u.locked_until = None
    u.failed_attempts = 0
    audit(db, "user.unlocked", user=actor, entity="user", entity_id=u.id, ip=client_ip(request))
    db.commit()
    return _out(u)


@router.post("/{user_id}/reset-2fa")
def reset_2fa(user_id: int, request: Request, actor: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.is_admin and not actor.is_admin:
        raise HTTPException(403, "Only administrators can reset administrator 2FA")
    u.totp_enabled = False
    u.totp_secret = None
    db.execute(update(UserSession).where(UserSession.user_id == u.id).values(revoked=True))
    audit(db, "user.2fa_reset", user=actor, entity="user", entity_id=u.id, ip=client_ip(request))
    db.commit()
    return _out(u)


@router.post("/{user_id}/revoke-sessions")
def revoke_sessions(
    user_id: int, request: Request, actor: User = Depends(require("users.manage")), db: Session = Depends(get_db)
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    db.execute(update(UserSession).where(UserSession.user_id == u.id).values(revoked=True))
    audit(db, "user.sessions_revoked", user=actor, entity="user", entity_id=u.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request, actor: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == actor.id:
        raise HTTPException(400, "You cannot delete your own account")
    if u.is_admin and not actor.is_admin:
        raise HTTPException(403, "Only administrators can delete administrator accounts")
    if u.is_admin and _active_admins(db) <= 1:
        raise HTTPException(400, "At least one active administrator is required")
    name = u.username
    db.delete(u)
    audit(db, "user.deleted", user=actor, entity="user", entity_id=user_id, ip=client_ip(request),
          details={"username": name})
    db.commit()
    return {"status": "ok"}
