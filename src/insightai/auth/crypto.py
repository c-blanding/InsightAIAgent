"""AES-GCM for browser storage state. The key never goes in the database."""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_VERSION = 1
_KEY_BYTES = 32


class AuthCryptoError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.code = "auth_decrypt_failed"


def load_data_key() -> bytes:
    raw = os.environ.get("AUTH_DATA_KEY", "").strip()
    if not raw:
        raise AuthCryptoError("AUTH_DATA_KEY is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AuthCryptoError("AUTH_DATA_KEY must be base64") from exc
    if len(key) != _KEY_BYTES:
        raise AuthCryptoError("AUTH_DATA_KEY must decode to 32 bytes")
    return key


def generate_data_key() -> str:
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


def encrypt_blob(plaintext: bytes, key: bytes, *, aad: bytes | None = None) -> tuple[bytes, bytes]:
    if len(key) != _KEY_BYTES:
        raise AuthCryptoError("Encryption key must be 32 bytes")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return ciphertext, nonce


def decrypt_blob(
    ciphertext: bytes,
    nonce: bytes,
    key: bytes,
    *,
    aad: bytes | None = None,
) -> bytes:
    if len(key) != _KEY_BYTES:
        raise AuthCryptoError("Encryption key must be 32 bytes")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise AuthCryptoError(
            "Saved session could not be decrypted with AUTH_DATA_KEY"
        ) from exc
