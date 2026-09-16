"""Web Push without a third-party library: RFC 8291 encryption, RFC 8292 VAPID.

Both are small, fully specified, and built on primitives ``cryptography``
already provides — a dependency for ninety lines is not worth the supply chain.
The supervisor's 5-minute escalation rung arrives this way (§3 Layer 2): a push
to the tablet or phone already in their hand.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _hmac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def encrypt(payload: bytes, *, p256dh: str, auth: str, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Encrypt one push message body as a single ``aes128gcm`` record (RFC 8291 §3)."""
    ua_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)

    # HKDF with SHA-256 where each output fits one block: HMAC(PRK, info || 0x01).
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def load_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """A PEM key, or the raw 32-byte private scalar in base64url (web-push style)."""
    text = value.strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(text.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("VAPID key must be an EC P-256 key")
        return key
    return ec.derive_private_key(int.from_bytes(b64url_decode(text), "big"), ec.SECP256R1())


def application_server_key(private_key: ec.EllipticCurvePrivateKey) -> str:
    """The public key a browser passes to ``pushManager.subscribe``."""
    return b64url_encode(_public_bytes(private_key.public_key()))


def vapid_headers(endpoint: str, private_key: ec.EllipticCurvePrivateKey, subject: str) -> dict:
    """``Authorization: vapid t=…, k=…`` for one push service (RFC 8292 §3)."""
    parts = urlsplit(endpoint)
    token = jwt.encode(
        {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(time.time()) + 12 * 3600,
            "sub": subject,
        },
        private_key,
        algorithm="ES256",
    )
    return {"Authorization": f"vapid t={token}, k={application_server_key(private_key)}"}


def generate_private_key() -> str:
    """A new VAPID private key as base64url, for ``scripts/generate_vapid_keys.py``."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url_encode(key.private_numbers().private_value.to_bytes(32, "big"))
