"""Admin CLI (run inside the backend container).

  python -m app.cli create-admin <username>     create an administrator (prompts for password)
  python -m app.cli reset-password <username>   set a new password and unlock the account
  python -m app.cli disable-2fa <username>      remove 2FA if the authenticator was lost
  python -m app.cli kill-switch on|off          emergency kill switch without the web UI
"""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import select, update

from . import state
from .audit import audit
from .db import init_db, session_scope
from .models import User, UserSession
from .security import hash_password, password_problems


def _ask_password(username: str) -> str:
    while True:
        p1 = getpass.getpass("New password: ")
        problems = password_problems(p1, username)
        if problems:
            print("Password needs: " + ", ".join(problems))
            continue
        if p1 != getpass.getpass("Repeat password: "):
            print("Passwords do not match")
            continue
        return p1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, arg = argv[0], argv[1]
    init_db()
    with session_scope() as db:
        state.ensure_defaults(db)
        if cmd == "create-admin":
            name = arg.strip().lower()
            if db.scalar(select(User).where(User.username == name)):
                print("User exists")
                return 1
            db.add(User(username=name, full_name="Administrator", password_hash=hash_password(_ask_password(name)),
                        is_admin=True, must_change_password=False, created_by="cli"))
            audit(db, "user.created_cli", details={"username": name, "is_admin": True})
            print(f"Administrator '{name}' created")
        elif cmd in ("reset-password", "disable-2fa"):
            u = db.scalar(select(User).where(User.username == arg.strip().lower()))
            if u is None:
                print("No such user")
                return 1
            if cmd == "reset-password":
                u.password_hash = hash_password(_ask_password(u.username))
                u.failed_attempts, u.locked_until = 0, None
            else:
                u.totp_enabled, u.totp_secret = False, None
            db.execute(update(UserSession).where(UserSession.user_id == u.id).values(revoked=True))
            audit(db, f"user.{cmd.replace('-', '_')}_cli", details={"username": u.username})
            print("Done")
        elif cmd == "kill-switch":
            tr = state.get_locked(db, "trading")
            tr["kill_switch"] = arg == "on"
            tr["kill_switch_reason"] = "engaged from CLI" if arg == "on" else ""
            state.put(db, "trading", tr, by="cli")
            audit(db, "trading.kill_switch", details={"engaged": arg == "on", "via": "cli"})
            print(f"Kill switch {'ENGAGED' if arg == 'on' else 'released'}")
        else:
            print(__doc__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
