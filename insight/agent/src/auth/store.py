"""Postgres store for encrypted Playwright storage state."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from auth.crypto import (
    KEY_VERSION,
    AuthCryptoError,
    decrypt_blob,
    encrypt_blob,
    load_data_key,
)
from auth.storage_state import (
    StorageStateError,
    has_session_data,
    normalize_origin,
    scope_storage_state,
)
from db.database import Database, DatabaseError, get_auth_database
from runtime_paths import auth_scratch_dir

_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ALLOWED_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})


class AuthStoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SavedSession:
    profile_id: str
    origin: str
    storage_state: dict[str, Any]
    expires_at: datetime


def validate_profile_id(profile_id: str) -> str:
    cleaned = profile_id.strip()
    if not _PROFILE_ID.fullmatch(cleaned):
        raise AuthStoreError(
            "auth_profile",
            "profile id must be 1-64 letters, digits, dots, underscores, or hyphens",
        )
    return cleaned


def validate_database_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise AuthStoreError(
            "auth_not_configured",
            "Database URL must be a postgresql:// URL",
        )
    if parsed.hostname.lower() in _LOCAL_HOSTS:
        return
    sslmode = (parse_qs(parsed.query).get("sslmode") or [""])[0]
    if sslmode not in _ALLOWED_SSLMODES:
        raise AuthStoreError(
            "auth_not_configured",
            "Remote database URL must set sslmode=require, verify-ca, or verify-full",
        )


def _ttl() -> timedelta:
    raw = os.environ.get("AUTH_SESSION_TTL_MINUTES", "60").strip()
    try:
        minutes = int(raw)
    except ValueError as exc:
        raise AuthStoreError(
            "auth_not_configured",
            "AUTH_SESSION_TTL_MINUTES must be a whole number",
        ) from exc
    if minutes < 1 or minutes > 24 * 60:
        raise AuthStoreError(
            "auth_not_configured",
            "AUTH_SESSION_TTL_MINUTES must be between 1 and 1440",
        )
    return timedelta(minutes=minutes)


def _db() -> Database:
    try:
        db = get_auth_database()
    except DatabaseError as exc:
        raise AuthStoreError("auth_not_configured", str(exc)) from exc
    validate_database_url(db.url)
    return db


def ensure_schema(db: Database | None = None) -> None:
    """Create auth_sessions (and other mapped tables) if missing."""
    database = db or _db()
    try:
        database.create_tables()
    except Exception as exc:
        raise AuthStoreError(
            "auth_not_configured",
            "Could not ensure auth_sessions exists. "
            "Check DATABASE_URL / AUTH_DATABASE_URL privileges, then retry.",
        ) from exc


def purge_expired(db: Database | None = None) -> int:
    database = db or _db()
    return database.purge_expired_auth_sessions()


def save_session(
    profile_id: str,
    origin: str,
    storage_state: dict[str, Any],
    *,
    ttl: timedelta | None = None,
) -> datetime:
    profile_id = validate_profile_id(profile_id)
    origin = normalize_origin(origin)
    scoped = scope_storage_state(storage_state, origin)
    if not has_session_data(scoped):
        raise AuthStoreError(
            "auth_empty",
            f"No cookies or localStorage for {origin}. Sign in, then save again.",
        )
    plaintext = json.dumps(scoped, separators=(",", ":")).encode("utf-8")
    try:
        ciphertext, nonce = encrypt_blob(
            plaintext,
            load_data_key(),
            aad=_aad(profile_id, origin),
        )
    except AuthCryptoError as exc:
        raise AuthStoreError("auth_not_configured", str(exc)) from exc
    if ttl is not None and not timedelta(minutes=1) <= ttl <= timedelta(hours=24):
        raise AuthStoreError(
            "auth_not_configured",
            "Session lifetime must be between 1 minute and 24 hours",
        )
    expires_at = datetime.now(timezone.utc) + (ttl or _ttl())
    db = _db()
    ensure_schema(db)
    purge_expired(db)
    db.upsert_auth_session(
        profile_id=profile_id,
        origin=origin,
        ciphertext=ciphertext,
        nonce=nonce,
        key_version=KEY_VERSION,
        expires_at=expires_at,
    )
    return expires_at


def load_session(profile_id: str) -> SavedSession:
    profile_id = validate_profile_id(profile_id)
    try:
        key = load_data_key()
    except AuthCryptoError as exc:
        raise AuthStoreError("auth_not_configured", str(exc)) from exc
    db = _db()
    ensure_schema(db)
    purge_expired(db)
    row = db.get_auth_session(profile_id)
    if row is None:
        raise AuthStoreError(
            "auth_expired",
            f"No unexpired session for profile {profile_id}",
        )
    if row.key_version != KEY_VERSION:
        raise AuthStoreError(
            "auth_decrypt_failed",
            f"Session {profile_id} uses an unsupported key version",
        )
    try:
        plaintext = decrypt_blob(
            bytes(row.ciphertext),
            bytes(row.nonce),
            key,
            aad=_aad(profile_id, row.origin),
        )
        storage_state = json.loads(plaintext)
    except (AuthCryptoError, json.JSONDecodeError) as exc:
        raise AuthStoreError(
            "auth_decrypt_failed",
            "Saved session could not be decrypted with AUTH_DATA_KEY",
        ) from exc
    return SavedSession(
        profile_id=profile_id,
        origin=row.origin,
        storage_state=storage_state,
        expires_at=row.expires_at,
    )


def list_sessions() -> list[dict[str, Any]]:
    db = _db()
    ensure_schema(db)
    purge_expired(db)
    rows = db.list_auth_sessions()
    return [
        {
            "profile_id": row.profile_id,
            "origin": row.origin,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]


def revoke_session(profile_id: str) -> bool:
    profile_id = validate_profile_id(profile_id)
    db = _db()
    ensure_schema(db)
    return db.delete_auth_session(profile_id)


def _aad(profile_id: str, origin: str) -> bytes:
    """Bind a ciphertext to its row so a swapped blob fails to decrypt."""
    return f"{profile_id}\n{origin}".encode()


def protect_path(path: Path) -> None:
    """Limit a scratch path to the current user. Best-effort on every OS."""
    if os.name == "nt":
        user = os.environ.get("USERNAME", "").strip()
        if not user:
            return
        rights = "(OI)(CI)F" if path.is_dir() else "F"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:{rights}"],
            check=False,
            capture_output=True,
        )
        return
    mode = 0o700 if path.is_dir() else 0o600
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def prepare_scratch_dir() -> Path:
    scratch = auth_scratch_dir()
    scratch.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(scratch, 0o700)
        except OSError:
            pass
    protect_path(scratch)
    _sweep_scratch(scratch)
    return scratch


def _write_private(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    protect_path(path)


def _sweep_scratch(scratch: Path | None = None) -> None:
    root = scratch if scratch is not None else auth_scratch_dir()
    if not root.exists():
        return
    cutoff = time.time() - 3600
    for path in root.glob("insight-auth-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def materialize_storage_state(profile_id: str, page_url: str) -> Path:
    """Write decrypted storage state to a scratch file the browser can read."""
    try:
        requested = normalize_origin(page_url)
    except StorageStateError as exc:
        raise AuthStoreError(exc.code, str(exc)) from exc
    session = load_session(profile_id)
    if normalize_origin(session.origin) != requested:
        raise AuthStoreError(
            "auth_origin_mismatch",
            f"Saved session is for {session.origin}, but this run targets {requested}",
        )
    scratch = prepare_scratch_dir()
    path = scratch / f"insight-auth-{uuid.uuid4().hex}.json"
    _write_private(path, json.dumps(session.storage_state, separators=(",", ":")))
    return path
