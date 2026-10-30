"""Password hashing (scrypt), session tokens, TOTP (RFC 6238) and permissions.

Only the Python standard library is used for cryptography here, so there are no
native build dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

# ----------------------------------------------------------------- passwords
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(expected)
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# A pre-computed hash used to keep login timing constant for unknown users.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problems(password: str, username: str = "") -> list[str]:
    problems = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if not any(c.islower() for c in password):
        problems.append("a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(not c.isalnum() for c in password):
        problems.append("a symbol")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    return problems


# ------------------------------------------------------------------- tokens
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_now(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    return _hotp(secret_b32, int((at or time.time()) // step))


def verify_totp(secret_b32: str, code: str, window: int = 1, step: int = 30) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    counter = int(time.time() // step)
    return any(hmac.compare_digest(_hotp(secret_b32, counter + w), code) for w in range(-window, window + 1))


def totp_uri(secret_b32: str, username: str, issuer: str) -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


# -------------------------------------------------------------- permissions
PERMISSIONS: dict[str, str] = {
    "dashboard.view": "View dashboard, account and positions",
    "market.view": "View market data and charts",
    "signals.view": "View strategy signals and risk checks",
    "trades.view": "View orders, positions and trade history",
    "backtest.run": "Run and view backtests, import historical data",
    "strategy.edit": "Change strategy parameters and instruments",
    "risk.edit": "Change risk limits (increases need password confirmation)",
    "trading.control": "Switch trading mode (Off / Paper / MT5 Demo) and reset limit locks",
    "killswitch.use": "Engage or release the kill switch, close positions",
    "trading.manual": "Place manual orders from the Live trading page (always risk-checked)",
    "ai.use": "Use the AI assistant / trading coach",
    "live.approve": "Approve live-trading gate and enable MT5 Live mode",
    "users.manage": "Create and manage users and permissions",
    "audit.view": "View audit trail and system events",
    "system.settings": "Change system settings (data source, notifications)",
}

ROLE_PRESETS: dict[str, list[str]] = {
    "viewer": ["dashboard.view", "market.view", "signals.view", "trades.view", "ai.use"],
    "analyst": ["dashboard.view", "market.view", "signals.view", "trades.view", "backtest.run", "ai.use"],
    "operator": [
        "dashboard.view",
        "market.view",
        "signals.view",
        "trades.view",
        "backtest.run",
        "trading.control",
        "killswitch.use",
        "trading.manual",
        "ai.use",
    ],
    "admin": list(PERMISSIONS.keys()),
}


def user_has(user, perm: str) -> bool:
    if user.is_admin:
        return True
    return perm in (user.permissions or [])


def effective_permissions(user) -> list[str]:
    if user.is_admin:
        return list(PERMISSIONS.keys())
    return [p for p in (user.permissions or []) if p in PERMISSIONS]
