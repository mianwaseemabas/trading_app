"""Off-server database backups.

Called by deploy/backup.sh on the server (nightly cron):
  record-local  --name F --bytes N        remember that a local backup was written
  send          --name F [--force] < F    encrypt with your backup password and send the file to your Telegram
To restore an encrypted copy on any machine with Docker (or Python + cryptography):
  BACKUP_PASSWORD='…' python -m app.backup_tool decrypt < trading-YYYYmmdd.sql.gz.qvx > trading.sql.gz
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"QVXB1"
TELEGRAM_LIMIT = 49 * 1024 * 1024


def _key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode())


def encrypt_bytes(data: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a Qivynex encrypted backup")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    return AESGCM(_key(password, salt)).decrypt(nonce, ct, MAGIC)


def send_document(token: str, chat: str, filename: str, data: bytes, caption: str, http=None) -> dict:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if http:
        return http(req)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _db():
    from .db import SessionLocal
    return SessionLocal()


def record_local(name: str, size: int) -> None:
    from . import state
    from .models import utcnow
    db = _db()
    try:
        st = state.get(db, "backup_status")
        st.update(expected=True, last_local_at=utcnow().isoformat(), last_local_name=name, last_local_bytes=size)
        state.put(db, "backup_status", st)
        db.commit()
    finally:
        db.close()


def send(name: str, data: bytes, force: bool = False, http=None) -> str:
    from . import state
    from .models import utcnow
    from .secretbox import decrypt
    from .services.backup_cfg import telegram_target
    db = _db()
    try:
        cfg = state.get(db, "backup")
        st = state.get(db, "backup_status")
        if not cfg.get("offsite_telegram"):
            return "off-server copy is switched off (System → Backups)"
        if not cfg.get("password_enc"):
            return "no backup password set (System → Backups)"
        last = st.get("last_offsite_at")
        if last and not force and (utcnow() - datetime.fromisoformat(last)).total_seconds() < 20 * 3600:
            return "already sent in the last 20 hours"
        blob = encrypt_bytes(data, decrypt(cfg["password_enc"]))
        if len(blob) > TELEGRAM_LIMIT:
            st.update(last_offsite_error=f"backup too large for Telegram ({len(blob) // 1_048_576} MB)")
            state.put(db, "backup_status", st)
            db.commit()
            return st["last_offsite_error"]
        token, chat = telegram_target(db)
        if not token or not chat:
            return "Telegram not configured"
        send_document(token, chat, f"{name}.qvx", blob, f"Qivynex encrypted DB backup {name} ({len(blob) // 1024} KB). "
                      "Keep it. Restore guide: System → Backups.", http=http)
        st.update(last_offsite_at=utcnow().isoformat(), last_offsite_name=f"{name}.qvx", last_offsite_bytes=len(blob), last_offsite_error="")
        state.put(db, "backup_status", st)
        db.commit()
        return "sent"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        st = state.get(db, "backup_status")
        st["last_offsite_error"] = str(e)[:300]
        state.put(db, "backup_status", st)
        db.commit()
        return f"failed: {e}"
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.backup_tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("record-local")
    a.add_argument("--name", required=True)
    a.add_argument("--bytes", type=int, default=0)
    b = sub.add_parser("send")
    b.add_argument("--name", required=True)
    b.add_argument("--force", action="store_true")
    sub.add_parser("decrypt")
    args = ap.parse_args(argv)
    if args.cmd == "record-local":
        record_local(args.name, args.bytes)
        print("recorded")
    elif args.cmd == "send":
        print(send(args.name, sys.stdin.buffer.read(), args.force))
    else:
        pw = os.environ.get("BACKUP_PASSWORD")
        if not pw:
            print("Set BACKUP_PASSWORD to your backup password", file=sys.stderr)
            return 2
        sys.stdout.buffer.write(decrypt_bytes(sys.stdin.buffer.read(), pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
