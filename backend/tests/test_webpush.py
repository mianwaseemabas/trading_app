"""Web Push payload encryption round-trip (RFC 8291) and VAPID header."""
import json
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services import webpush as wp


def _decrypt(body: bytes, ua_key, auth: bytes) -> bytes:
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    ua_pub = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = wp._hkdf_expand(wp._hkdf_extract(auth, shared), b"WebPush: info\x00" + ua_pub + as_pub, 32)
    prk = wp._hkdf_extract(salt, ikm)
    pt = AESGCM(wp._hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(wp._hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12), ct, None)
    assert rs == 4096 and pt.endswith(b"\x02")
    return pt[:-1]


def test_payload_roundtrip():
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    msg = json.dumps({"title": "AUTO TRADE", "body": "BTCUSDT BUY"}).encode()
    body = wp.encrypt_payload(msg, wp.b64u(ua_pub), wp.b64u(auth))
    assert _decrypt(body, ua, auth) == msg


def test_vapid_jwt_verifies():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = wp.b64u(wp._pub_raw(k))
    h = wp.vapid_header(k, pub, "https://fcm.googleapis.com/fcm/send/abc")
    tok = h.split("t=")[1].split(",")[0]
    head, claims, sig = tok.split(".")
    c = json.loads(wp.unb64u(claims))
    assert c["aud"] == "https://fcm.googleapis.com" and h.endswith(pub)
    raw = wp.unb64u(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    k.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
