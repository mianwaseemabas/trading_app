"""Application configuration, loaded from environment variables.

Secrets (database password, bridge secret, Telegram token) must come from the
environment / .env file and are never committed to source control.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "production"))
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "Qivynex Trade"))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "https://trade.qivynex.com"))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://trader:trader@localhost:5432/trading"
        )
    )
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", ""))

    # Sessions / auth
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", True))
    session_idle_minutes: int = field(default_factory=lambda: _int("SESSION_IDLE_MINUTES", 30))
    session_max_hours: int = field(default_factory=lambda: _int("SESSION_MAX_HOURS", 12))
    max_failed_logins: int = field(default_factory=lambda: _int("MAX_FAILED_LOGINS", 5))
    lockout_minutes: int = field(default_factory=lambda: _int("LOCKOUT_MINUTES", 15))
    ip_max_failed_per_15min: int = field(default_factory=lambda: _int("IP_MAX_FAILED_PER_15MIN", 20))

    # First-run bootstrap admin (only used when the users table is empty)
    initial_admin_username: str = field(default_factory=lambda: os.getenv("INITIAL_ADMIN_USERNAME", ""))
    initial_admin_password: str = field(default_factory=lambda: os.getenv("INITIAL_ADMIN_PASSWORD", ""))

    # MT5 bridge (Windows execution host)
    bridge_key_id: str = field(default_factory=lambda: os.getenv("BRIDGE_KEY_ID", "mt5-bridge-1"))
    bridge_secret: str = field(default_factory=lambda: os.getenv("BRIDGE_SECRET", ""))
    bridge_allowed_ips: list[str] = field(default_factory=lambda: _list("BRIDGE_ALLOWED_IPS"))
    bridge_max_clock_skew_s: int = field(default_factory=lambda: _int("BRIDGE_MAX_CLOCK_SKEW_S", 30))

    # Optional Telegram notifications
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Worker
    worker_interval_s: int = field(default_factory=lambda: _int("WORKER_INTERVAL_S", 10))

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "test"}

    def validate(self) -> None:
        if not self.is_dev:
            if len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be set to at least 32 random characters")
            if self.bridge_secret and len(self.bridge_secret) < 32:
                raise RuntimeError("BRIDGE_SECRET must be at least 32 characters")


settings = Settings()
