"""Web Push (RFC 8030/8291/8292) with VAPID — implemented on `cryptography` only, no extra dependency.

Notifications reach the installed phone app (PWA) or a desktop browser without Telegram.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..secretbox import decrypt, encrypt

log = logging.getLogger("webpush")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _pub_raw(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_keys(db: Session) -> tuple[ec.EllipticCurvePrivateKey, str]:
    st = state.get(db, "push")
    if not st.get("private_enc"):
        k = ec.generate_private_key(ec.SECP256R1())
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        st = {**st, "private_enc": encrypt(pem), "public": b64u(_pub_raw(k))}
        state.put(db, "push", st)
        db.flush()
    k = serialization.load_pem_private_key(decrypt(st["private_enc"]).encode(), None)
    return k, st["public"]


def vapid_header(key, public: str, endpoint: str, subject: str = "mailto:admin@qivynex.com") -> str:
    u = urllib.parse.urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject},
                             separators=(",", ":")).encode())
    der = key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{claims}.{sig}, k={public}"


def encrypt_payload(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, as_key=None) -> bytes:
    """aes128gcm content encoding (RFC 8188 + RFC 8291)."""
    ua_pub = unb64u(p256dh)
    auth_secret = unb64u(auth)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_raw(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, shared), b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!I", 4096) + bytes([len(as_pub)]) + as_pub + ct


def send(db: Session, title: str, body: str = "", link: str = "#/alerts", user_id: int | None = None, http=None) -> tuple[int, int]:
    """Send to every saved subscription (optionally one user). Returns (sent, failed). Dead subscriptions are removed."""
    from ..models import PushSubscription, utcnow
    q = select(PushSubscription)
    if user_id:
        q = q.where(PushSubscription.user_id == user_id)
    subs = db.scalars(q).all()
    if not subs:
        return 0, 0
    key, public = vapid_keys(db)
    data = json.dumps({"title": title[:120], "body": body[:400], "link": link}).encode()
    ok = bad = 0
    for s in subs:
        try:
            req = urllib.request.Request(s.endpoint, data=encrypt_payload(data, s.p256dh, s.auth), method="POST", headers={
                "TTL": "86400", "Urgency": "high", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                "Authorization": vapid_header(key, public, s.endpoint)})
            code = http(req) if http else urllib.request.urlopen(req, timeout=10).status
            if code in (200, 201, 202):
                ok += 1
                s.last_ok, s.fails = utcnow(), 0
            else:
                raise urllib.error.HTTPError(s.endpoint, code, "push failed", None, None)
        except urllib.error.HTTPError as e:
            bad += 1
            if e.code in (404, 410):
                db.delete(s)
            else:
                s.fails = (s.fails or 0) + 1
        except Exception as e:  # noqa: BLE001
            bad += 1
            s.fails = (s.fails or 0) + 1
            log.info("push to %s failed: %s", s.endpoint[:40], e)
        if s in db and (s.fails or 0) >= 10:
            db.delete(s)
    return ok, bad
