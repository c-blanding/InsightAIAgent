"""Timeline + action-log helpers for a QA run thread.

Events are written best-effort to Postgres ``run_events``. ``assemble_timeline``
merges those with ``run_artifacts`` and ``run_logs`` into one ordered view.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from auth.redact import redact_text

logger = logging.getLogger(__name__)

# Canonical event types used by the harness.
EVENT_SESSION_START = "session_start"
EVENT_SESSION_END = "session_end"
EVENT_VIDEO_START = "video_start"
EVENT_VIDEO_STOP = "video_stop"
EVENT_STEP_START = "step_start"
EVENT_STEP_END = "step_end"
EVENT_TOOL = "tool"
EVENT_SCREENSHOT = "screenshot"
EVENT_SNAPSHOT = "snapshot"
EVENT_CONSOLE = "console"
EVENT_NETWORK = "network"
EVENT_AUTH_RESTORE = "auth_restore"
EVENT_AUTH_LOGIN = "auth_login"
EVENT_AUTH_SAVED = "auth_saved"
EVENT_UPLOAD = "upload"
EVENT_ERROR = "error"

_DETAIL_CAP = 4_000


def _db():
    if not (os.environ.get("DATABASE_URL") or "").strip():
        return None
    try:
        from db import get_database

        db = get_database()
        db.create_tables()
        return db
    except Exception:
        logger.debug("timeline db unavailable", exc_info=True)
        return None


def emit_event(
    thread_id: str | None,
    event_type: str,
    title: str,
    *,
    step: int | None = None,
    tool: str | None = None,
    detail: str | None = None,
    status: str = "ok",
    ref_kind: str | None = None,
    ref_bucket: str | None = None,
    ref_object_key: str | None = None,
) -> dict[str, Any] | None:
    """Insert one timeline/action event. Never raises to callers."""
    key = (thread_id or "").strip() or "local"
    text = redact_text(detail) if detail else None
    if text and len(text) > _DETAIL_CAP:
        text = text[:_DETAIL_CAP] + "\n...[truncated]"
    db = _db()
    if db is None:
        logger.info("[event:%s] %s", event_type, title)
        return None
    try:
        row = db.insert_run_event(
            thread_id=key,
            event_type=event_type,
            title=title,
            step=step,
            tool=tool,
            detail=text,
            status=status or "ok",
            ref_kind=ref_kind,
            ref_bucket=ref_bucket,
            ref_object_key=ref_object_key,
        )
        return {
            "id": str(row.id),
            "thread_id": row.thread_id,
            "step": row.step,
            "event_type": row.event_type,
            "tool": row.tool,
            "title": row.title,
            "detail": row.detail,
            "status": row.status,
            "ref_kind": row.ref_kind,
            "ref_bucket": row.ref_bucket,
            "ref_object_key": row.ref_object_key,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "source": "run_events",
        }
    except Exception:
        logger.debug("insert_run_event failed", exc_info=True)
        return None


def emit_from_context(
    event_type: str,
    title: str,
    *,
    tool: str | None = None,
    detail: str | None = None,
    status: str = "ok",
    ref_kind: str | None = None,
    ref_bucket: str | None = None,
    ref_object_key: str | None = None,
) -> dict[str, Any] | None:
    """Emit using the current evidence step context when available."""
    try:
        from evidence import context as evidence_context

        ctx = evidence_context.current()
    except Exception:
        ctx = None
    thread_id = ctx.thread_id if ctx is not None else "local"
    step = ctx.step if ctx is not None else None
    return emit_event(
        thread_id,
        event_type,
        title,
        step=step,
        tool=tool,
        detail=detail,
        status=status,
        ref_kind=ref_kind,
        ref_bucket=ref_bucket,
        ref_object_key=ref_object_key,
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def assemble_timeline(thread_id: str) -> Timeline:
    """Build a merged timeline + action log for ``thread_id``.

    Primary spine: ``run_events``. Also folds in ``run_artifacts`` and
    ``run_logs`` that may not have a matching event yet (legacy / best-effort).
    """
    from utils.objects import Timeline

    key = (thread_id or "").strip() or "local"
    db = _db()
    events: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []

    if db is None:
        return Timeline(
            thread_id=key,
            error="DATABASE_URL unset or database unavailable",
        )

    try:
        for row in db.list_run_events(key):
            item = {
                "id": str(row.id),
                "t": _iso(row.created_at),
                "step": row.step,
                "event_type": row.event_type,
                "tool": row.tool,
                "title": row.title,
                "detail": row.detail,
                "status": row.status,
                "ref": (
                    {
                        "kind": row.ref_kind,
                        "bucket": row.ref_bucket,
                        "object_key": row.ref_object_key,
                    }
                    if row.ref_kind or row.ref_object_key
                    else None
                ),
                "source": "run_events",
            }
            events.append(item)
            if row.event_type == EVENT_TOOL:
                actions.append(item)
    except Exception:
        logger.debug("list_run_events failed", exc_info=True)

    seen_refs: set[tuple[str | None, str | None, str | None]] = set()
    for item in events:
        ref = item.get("ref") or {}
        seen_refs.add((ref.get("kind"), ref.get("bucket"), ref.get("object_key")))

    try:
        for row in db.list_run_artifacts(key):
            art = {
                "id": str(row.id),
                "t": _iso(row.created_at),
                "step": row.step,
                "kind": row.kind,
                "bucket": row.bucket,
                "object_key": row.object_key,
                "content_type": row.content_type,
                "byte_size": row.byte_size,
            }
            artifacts.append(art)
            key_ref = (row.kind, row.bucket, row.object_key)
            if key_ref not in seen_refs:
                events.append(
                    {
                        "id": f"artifact-{row.id}",
                        "t": art["t"],
                        "step": row.step,
                        "event_type": row.kind,
                        "tool": None,
                        "title": f"{row.kind}: {row.object_key}",
                        "detail": None,
                        "status": "ok",
                        "ref": {
                            "kind": row.kind,
                            "bucket": row.bucket,
                            "object_key": row.object_key,
                        },
                        "source": "run_artifacts",
                    }
                )
                seen_refs.add(key_ref)
    except Exception:
        logger.debug("list_run_artifacts failed", exc_info=True)

    try:
        for row in db.list_run_logs(key):
            log = {
                "id": str(row.id),
                "t": _iso(row.created_at),
                "step": row.step,
                "kind": row.kind,
                "source": row.source,
                "message": row.message,
            }
            logs.append(log)
            # Snapshots/console/network already often have run_events; still
            # include log-only rows so older runs show up on the timeline.
            if row.kind in {"console", "network", "snapshot"}:
                events.append(
                    {
                        "id": f"log-{row.id}",
                        "t": log["t"],
                        "step": row.step,
                        "event_type": row.kind,
                        "tool": None,
                        "title": f"{row.kind} log",
                        "detail": (row.message or "")[:500],
                        "status": "ok",
                        "ref": None,
                        "source": "run_logs",
                    }
                )
    except Exception:
        logger.debug("list_run_logs failed", exc_info=True)

    def _sort_key(item: dict[str, Any]) -> tuple:
        return (item.get("t") or "", str(item.get("id") or ""))

    events.sort(key=_sort_key)
    actions.sort(key=_sort_key)

    chapters: dict[int | str, list[dict[str, Any]]] = {}
    for item in events:
        chapter = item.get("step") if item.get("step") is not None else "run"
        chapters.setdefault(chapter, []).append(item)

    chapter_list = [
        {
            "step": key if key != "run" else None,
            "label": f"Step {key}" if key != "run" else "Run",
            "events": chapters[key],
        }
        for key in sorted(
            chapters.keys(),
            key=lambda k: (k == "run", k if isinstance(k, int) else 0),
        )
    ]

    return Timeline.model_validate(
        {
            "thread_id": key,
            "events": events,
            "actions": actions,
            "artifacts": artifacts,
            "logs": logs,
            "chapters": chapter_list,
        }
    )
