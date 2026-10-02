"""Capture the current Playwright browser session into the encrypted store."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from auth.storage_state import (
    StorageStateError,
    has_session_data,
    normalize_origin,
    scope_storage_state,
)
from auth.store import AuthStoreError, prepare_scratch_dir, save_session
from mcp_clients.playwright import PlaywrightMCP, PlaywrightToolError
from runtime_paths import absolute_path


def default_profile_id(page_url: str) -> str:
    origin = normalize_origin(page_url)
    host = urlsplit(origin).hostname or "session"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", host).strip("-._")[:48]
    return f"auto-{cleaned or 'session'}"


def resume_confirmed(resume: Any) -> bool:
    """Studio Continue often resumes with null/empty — treat that as confirm."""
    if resume is False:
        return False
    if resume is None or resume is True or resume == "":
        return True
    if isinstance(resume, dict):
        if resume.get("cancel") or resume.get("cancelled"):
            return False
        if resume.get("confirmed") is False:
            return False
        return True
    if isinstance(resume, str):
        return resume.strip().lower() not in {"cancel", "cancelled", "false", "no"}
    return True


def _read_storage_state(capture_path: Path) -> dict[str, Any]:
    if not capture_path.is_file():
        raise AuthStoreError(
            "auth_capture",
            "Playwright did not write a storage state file. "
            "Start MCP with --caps=storage.",
        )
    try:
        raw = json.loads(capture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthStoreError(
            "auth_capture",
            "Storage state file was not valid JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise AuthStoreError(
            "auth_capture",
            "Storage state file was not a JSON object",
        )
    return raw


async def persist_open_browser_session(
    playwright: PlaywrightMCP,
    *,
    profile_id: str,
    page_url: str,
) -> datetime:
    """Export storage state from the open browser and encrypt it into Postgres."""
    origin = normalize_origin(page_url)
    scratch = await asyncio.to_thread(prepare_scratch_dir)
    capture_path = absolute_path(scratch / f"insight-auth-capture-{uuid.uuid4().hex}.json")
    try:
        await playwright.call_mcp(
            "browser_storage_state",
            {"filename": str(capture_path)},
        )
        try:
            raw = await asyncio.to_thread(_read_storage_state, capture_path)
        except AuthStoreError:
            raise
        scoped = scope_storage_state(raw, origin)
        if not has_session_data(scoped):
            raise AuthStoreError(
                "auth_empty",
                f"No cookies or localStorage for {origin}. Sign in, then Continue again.",
            )
        return await asyncio.to_thread(save_session, profile_id, origin, scoped)
    except StorageStateError as exc:
        raise AuthStoreError(exc.code, str(exc)) from exc
    except PlaywrightToolError as exc:
        raise AuthStoreError(exc.code, str(exc)) from exc
    finally:
        try:
            await asyncio.to_thread(capture_path.unlink, missing_ok=True)
        except OSError:
            pass
