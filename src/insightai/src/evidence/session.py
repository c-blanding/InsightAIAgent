"""One Playwright MCP client per LangGraph thread for the whole run.

Opens the client once, starts video recording, shares the client with
``execute_plan`` and ``wait_for_login``, then stops video, uploads, and closes
on finish. Evidence upload is best-effort.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from auth.store import materialize_storage_state
from evidence.store import SCREENSHOT_BUCKET, VIDEO_BUCKET, upload_file
from mcp_clients.playwright import PlaywrightMCP, PlaywrightToolError
from runtime_paths import absolute_path, media_dir, REPO_ROOT
from utils.logging import Logging

logger = logging.getLogger(__name__)

_THREAD_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_THREAD_ID = "local"
_VIDEO_WAIT_SECONDS = 30.0
_STALE_MEDIA_SECONDS = 3600
# Relative to MCP --output-dir / INSIGHT_MEDIA_DIR (not an absolute path).
_VIDEO_REL = "run.webm"

_lock = asyncio.Lock()
_sessions: dict[str, _HeldSession] = {}


@dataclass
class _HeldSession:
    client: PlaywrightMCP
    thread_id: str
    video_path: Path
    video_rel: str
    video_started: bool = False
    auth_profile_applied: str | None = None
    releasing: bool = False


def normalize_thread_id(raw: str | None) -> str:
    if raw is None:
        return _DEFAULT_THREAD_ID
    cleaned = _THREAD_ID_RE.sub("-", str(raw).strip()).strip("-._")[:64]
    return cleaned or _DEFAULT_THREAD_ID


def thread_id_from_config(config: RunnableConfig | None) -> str:
    if not config:
        return _DEFAULT_THREAD_ID
    configurable = config.get("configurable") or {}
    return normalize_thread_id(configurable.get("thread_id"))


def local_run_dir(thread_id: str) -> Path:
    return media_dir() / "runs" / normalize_thread_id(thread_id)


def _video_rel(thread_id: str) -> str:
    """Filename passed to Playwright MCP (resolved against --output-dir)."""
    return f"runs/{normalize_thread_id(thread_id)}/{_VIDEO_REL}"


def _sweep_stale_media() -> None:
    root = media_dir()
    runs = root / "runs"
    if runs.is_dir():
        cutoff = time.time() - _STALE_MEDIA_SECONDS
        for path in runs.iterdir():
            try:
                if path.is_dir() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
    # Loose MCP defaults (video-*.webm / traces) left at media root.
    if root.is_dir():
        cutoff = time.time() - _STALE_MEDIA_SECONDS
        for path in root.iterdir():
            try:
                if (
                    path.is_file()
                    and path.suffix.lower() in {".webm", ".png", ".zip", ".log"}
                    and path.stat().st_mtime < cutoff
                ):
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    # Legacy accident: relative MCP filenames wrote under <repo>/runs.
    legacy = REPO_ROOT / "runs"
    if legacy.is_dir() and legacy != runs:
        try:
            shutil.rmtree(legacy, ignore_errors=True)
        except OSError:
            pass


def _cleanup_run_dir(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)


def _upload_pending_screenshots(run_dir: Path, thread_id: str) -> tuple[list[dict[str, Any]], bool]:
    """Upload PNGs still on disk; delete each after a successful upload.

    Returns ``(uploaded_artifacts, still_pending)``. ``still_pending`` is True when
    at least one local PNG could not be uploaded (keep the run dir for retry).
    """
    uploaded: list[dict[str, Any]] = []
    still_pending = False
    if not run_dir.is_dir():
        return uploaded, still_pending
    try:
        pngs = sorted(p for p in run_dir.rglob("*.png") if p.is_file() and p.stat().st_size > 0)
    except OSError:
        return uploaded, still_pending
    media_root = media_dir()
    for path in pngs:
        try:
            rel = path.relative_to(media_root).as_posix()
        except ValueError:
            rel = f"runs/{thread_id}/{path.name}"
        step: int | None = None
        for part in path.parts:
            if part.startswith("step-"):
                try:
                    step = int(part.split("-", 1)[1])
                except ValueError:
                    step = None
                break
        artifact = upload_file(
            path,
            bucket=SCREENSHOT_BUCKET,
            object_key=rel,
            content_type="image/png",
            kind="screenshot",
            thread_id=thread_id,
            step=step,
        )
        if artifact:
            uploaded.append(artifact)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            still_pending = True
    return uploaded, still_pending


def _find_video_file(preferred: Path, run_dir: Path) -> Path | None:
    """Locate the recorded webm after stop (MCP may rename or nest files)."""
    try:
        if preferred.is_file() and preferred.stat().st_size > 0:
            return preferred
    except OSError:
        pass
    if not run_dir.is_dir():
        return None
    candidates: list[Path] = []
    try:
        candidates = [
            p
            for p in run_dir.rglob("*.webm")
            if p.is_file() and p.stat().st_size > 0
        ]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _existing_webm(raw: str | Path) -> Path | None:
    candidate = Path(str(raw).strip().strip("\"'"))
    options: list[Path] = []
    if candidate.is_absolute():
        options.append(candidate)
    else:
        options.extend([REPO_ROOT / candidate, media_dir() / candidate])
    for path in options:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return absolute_path(path)
        except (OSError, ValueError):
            continue
    return None


def _path_from_stop_result(result: Any) -> Path | None:
    """Best-effort parse of browser_stop_video file references."""
    text_parts: list[str] = []
    content = getattr(result, "content", None)
    if isinstance(content, str):
        text_parts.append(content)
    elif content:
        for block in content:
            text = getattr(block, "text", None)
            if text:
                text_parts.append(text)
            elif isinstance(block, dict) and block.get("text"):
                text_parts.append(str(block["text"]))
            uri = getattr(block, "uri", None) or (
                block.get("uri") if isinstance(block, dict) else None
            )
            if uri:
                found = _existing_webm(str(uri).removeprefix("file://"))
                if found is not None:
                    return found
    blob = "\n".join(text_parts)
    for match in re.finditer(r"[^\s\]\)]+\.webm", blob):
        found = _existing_webm(match.group(0))
        if found is not None:
            return found
    return None


async def _wait_for_video_file(
    preferred: Path, run_dir: Path, timeout: float = _VIDEO_WAIT_SECONDS
) -> Path | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = await asyncio.to_thread(_find_video_file, preferred, run_dir)
        if found is not None:
            return found
        await asyncio.sleep(0.2)
    return await asyncio.to_thread(_find_video_file, preferred, run_dir)


async def session_for(thread_id: str | None) -> PlaywrightMCP:
    """Open or reuse the Playwright MCP client for this thread and start video."""
    key = normalize_thread_id(thread_id)

    await asyncio.to_thread(_sweep_stale_media)

    async with _lock:
        held = _sessions.get(key)
        if held is not None and held.client._entered and not held.releasing:
            return held.client

        run_dir = local_run_dir(key)
        await asyncio.to_thread(media_dir().mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(run_dir.mkdir, parents=True, exist_ok=True)
        video_rel = _video_rel(key)
        # Absolute path under INSIGHT_MEDIA_DIR / MCP --output-dir.
        # Relative names resolve against MCP workspace root (repo), not output-dir.
        video_path = absolute_path(media_dir() / video_rel)

        client = PlaywrightMCP()
        await client.start()
        held = _HeldSession(
            client=client,
            thread_id=key,
            video_path=video_path,
            video_rel=video_rel,
        )
        # Register before further awaits so CancelledError cannot leak the client.
        _sessions[key] = held

        try:
            from evidence.timeline import EVENT_SESSION_START, emit_event

            emit_event(key, EVENT_SESSION_START, "Playwright session opened")
        except Exception:
            pass

        if not client.has_tool("browser_start_video"):
            logger.warning(
                "Playwright MCP at %s has no browser_start_video "
                "(need --caps=devtools). Restart with: %s",
                client.url,
                client.start_hint(),
            )
            return client

        try:
            await client.call_mcp(
                "browser_start_video",
                {"filename": str(video_path)},
            )
            held.video_started = True
            try:
                from evidence.timeline import EVENT_VIDEO_START, emit_event

                emit_event(
                    key,
                    EVENT_VIDEO_START,
                    "Video recording started",
                    tool="browser_start_video",
                    detail=str(video_path),
                )
            except Exception:
                pass
        except PlaywrightToolError:
            logger.warning(
                "browser_start_video failed for thread %s; continuing without recording. "
                "Start MCP with: %s",
                key,
                client.start_hint(),
                exc_info=True,
            )

        return client


async def apply_auth_if_needed(thread_id: str | None, profile_id: str, url: str) -> None:
    """Restore encrypted cookies once per profile on the shared client."""
    key = normalize_thread_id(thread_id)
    profile = (profile_id or "").strip()
    if not profile:
        return

    async with _lock:
        held = _sessions.get(key)
        if held is None or held.releasing or held.auth_profile_applied == profile:
            return
        client = held.client

    if not client.has_tool("browser_set_storage_state"):
        logger.warning(
            "Playwright MCP missing browser_set_storage_state "
            "(need --caps=storage). Start with: %s",
            client.start_hint(),
        )
        return

    path = await asyncio.to_thread(materialize_storage_state, profile, url)
    try:
        async with _lock:
            current = _sessions.get(key)
            if current is None or current is not held or current.releasing:
                return
            if current.auth_profile_applied == profile:
                return
        # Auth scratch lives outside output-dir; absolute path is required.
        await client.call_mcp(
            "browser_set_storage_state",
            {"filename": str(absolute_path(path))},
        )
        async with _lock:
            current = _sessions.get(key)
            if current is held and not current.releasing:
                current.auth_profile_applied = profile
        try:
            from evidence.timeline import EVENT_AUTH_RESTORE, emit_event

            emit_event(
                key,
                EVENT_AUTH_RESTORE,
                f"Restored auth profile {profile}",
                tool="browser_set_storage_state",
                detail=profile,
            )
        except Exception:
            pass
    finally:
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            pass


def mark_auth_applied(thread_id: str | None, profile_id: str) -> None:
    """Mark that the live browser already has this profile (e.g. after login)."""
    key = normalize_thread_id(thread_id)
    profile = (profile_id or "").strip()
    if not profile:
        return
    held = _sessions.get(key)
    if held is not None:
        held.auth_profile_applied = profile


async def release(thread_id: str | None, *, finished: bool = True) -> dict[str, Any] | None:
    """Stop video, upload when finished, close the client, and clear local media.

    Idempotent. Safe to call when no session was opened.
    """
    run_log = Logging(thread_id=thread_id or _DEFAULT_THREAD_ID, step=None)
    key = normalize_thread_id(thread_id)
    async with _lock:
        held = _sessions.pop(key, None)
        if held is None:
            return None
        held.releasing = True

    artifact: dict[str, Any] | None = None
    local_video = held.video_path
    run_dir = local_run_dir(key)
    screenshots_pending = False
    try:
        if finished:
            # Refs already live on step_findings; flush any local PNGs now.
            pending, screenshots_pending = await asyncio.to_thread(
                _upload_pending_screenshots, run_dir, key
            )
            if pending:
                await asyncio.to_thread(
                    run_log.info,
                    f"Uploaded {len(pending)} screenshot(s) at end of run",
                )
                try:
                    from evidence.timeline import EVENT_UPLOAD, emit_event

                    emit_event(
                        key,
                        EVENT_UPLOAD,
                        f"Uploaded {len(pending)} screenshot(s)",
                        detail=str(len(pending)),
                    )
                except Exception:
                    pass

        if finished and held.video_started:
            stop_result: Any = None
            try:
                stop_result = await held.client.call_mcp("browser_stop_video", {})
                try:
                    from evidence.timeline import EVENT_VIDEO_STOP, emit_event

                    emit_event(
                        key,
                        EVENT_VIDEO_STOP,
                        "Video recording stopped",
                        tool="browser_stop_video",
                    )
                except Exception:
                    pass
            except PlaywrightToolError:
                logger.warning(
                    "browser_stop_video failed for thread %s",
                    key,
                    exc_info=True,
                )
            from_stop = (
                await asyncio.to_thread(_path_from_stop_result, stop_result)
                if stop_result is not None
                else None
            )
            if from_stop is not None:
                local_video = from_stop
            found = await _wait_for_video_file(local_video, run_dir)
            if found is not None:
                local_video = found
                artifact = await asyncio.to_thread(
                    upload_file,
                    local_video,
                    bucket=VIDEO_BUCKET,
                    object_key=f"runs/{key}/video.webm",
                    content_type="video/webm",
                    kind="video",
                    thread_id=key,
                    step=None,
                )
                if artifact:
                    await asyncio.to_thread(
                        run_log.info,
                        f"Uploaded video to Neon bucket {artifact['bucket']}/{artifact['object_key']}",
                    )
                    try:
                        from evidence.timeline import EVENT_UPLOAD, emit_event

                        emit_event(
                            key,
                            EVENT_UPLOAD,
                            "Uploaded run video",
                            ref_kind="video",
                            ref_bucket=artifact.get("bucket"),
                            ref_object_key=artifact.get("object_key"),
                        )
                    except Exception:
                        pass
                else:
                    logger.warning(
                        "Video recorded at %s but upload skipped/failed "
                        "(set AWS_* Neon Object Storage env to persist it)",
                        local_video,
                    )
            else:
                logger.warning(
                    "Video file missing or empty after stop (expected %s under %s)",
                    local_video,
                    run_dir,
                )
    finally:
        try:
            await held.client.stop()
        except Exception:
            logger.warning("Failed to stop Playwright MCP for thread %s", key, exc_info=True)
        if finished:
            exists = await asyncio.to_thread(local_video.exists)
            video_ok = artifact is not None or not held.video_started or not exists
            if video_ok and not screenshots_pending:
                await asyncio.to_thread(_cleanup_run_dir, run_dir)
        try:
            from evidence.timeline import EVENT_SESSION_END, emit_event

            emit_event(key, EVENT_SESSION_END, "Playwright session closed")
        except Exception:
            pass

    return artifact
