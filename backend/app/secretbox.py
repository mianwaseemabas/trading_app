"""Encryption for secrets stored in the database (broker credentials, AI API keys).

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from SECRET_KEY, so a
database dump alone does not reveal credentials. Changing SECRET_KEY makes
stored secrets unreadable (they must then be re-entered in the dashboard).
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    material = (settings.secret_key or "dev-insecure-secret-key").encode()
    key = hashlib.sha256(b"qivynex-trade-secretbox-v1|" + material).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def mask(value: str) -> str:
    if not value:
        return ""
    return ("•" * 6) + value[-4:] if len(value) > 6 else "••••"
