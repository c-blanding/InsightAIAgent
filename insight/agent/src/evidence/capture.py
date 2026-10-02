"""Harness-owned screenshot capture plus console/network run logging."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from auth.redact import redact_text
from evidence import context as evidence_context
from evidence.store import SCREENSHOT_BUCKET, upload_file
from mcp_clients.playwright import PlaywrightMCP, PlaywrightToolError
from runtime_paths import absolute_path, media_dir
from utils.logging import Logging

logger = logging.getLogger(__name__)

_MAX_LOG_BYTES = 32_768
_CONSOLE_TAIL = 30
_LOGIN_MARKERS = re.compile(
    r"(?i)\b(password|passwd|sign[\s-]?in|log[\s-]?in|username|email address)\b"
)

_SCREENSHOT_AFTER = frozenset(
    {
        "browser_navigate",
        "browser_navigate_back",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_press_key",
    }
)
_NETWORK_AFTER = frozenset(
    {
        "browser_navigate",
        "browser_navigate_back",
        "browser_fill_form",
    }
)


def looks_like_login_page(text: str | None) -> bool:
    if not text:
        return False
    hits = _LOGIN_MARKERS.findall(text)
    return len(hits) >= 2 or ("password" in text.lower() and "sign" in text.lower())


def _tool_failed(result: Any) -> bool:
    if result is None:
        return False
    if getattr(result, "is_error", False):
        return True
    text = tool_result_text(result).lower()
    return text.startswith("error") or "tool call failed" in text


def tool_result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    data = getattr(result, "data", None)
    if data is not None and not parts:
        parts.append(str(data))
    return "\n".join(parts)


def _cap(text: str, limit: int = _MAX_LOG_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return (b"...[truncated]\n" + encoded[-(limit - 16) :]).decode("utf-8", errors="replace")


def _filter_console(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if re.search(r"(?i)\b(error|exception|failed)\b", line)]
    tail = lines[-_CONSOLE_TAIL:]
    merged: list[str] = []
    seen: set[str] = set()
    for line in errors + tail:
        if line not in seen:
            seen.add(line)
            merged.append(line)
    return "\n".join(merged)


def _filter_network(text: str) -> str:
    """Keep failed / HTTP >= 400 lines when the dump is line-oriented."""
    lines = text.splitlines()
    interesting = [
        line
        for line in lines
        if re.search(r"(?i)\b(failed|error|timeout)\b", line)
        or re.search(r"\b([4-5]\d{2})\b", line)
    ]
    if interesting:
        return "\n".join(interesting)
    return text


def _run_logger(ctx) -> Logging:
    return Logging(name="harness", thread_id=ctx.thread_id, step=ctx.step)


async def capture_screenshot(client: PlaywrightMCP, *, tool_name: str) -> dict[str, Any] | None:
    ctx = evidence_context.current()
    if ctx is None or ctx.skip_screenshots:
        return None
    seq = evidence_context.next_seq()
    # Absolute under media_dir: relative names resolve against MCP workspace root.
    rel = f"runs/{ctx.thread_id}/step-{ctx.step}/{seq:03d}-{tool_name.replace('browser_', '')}.png"
    path = absolute_path(media_dir() / rel)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    try:
        await client.call_mcp(
            "browser_take_screenshot",
            {"filename": str(path), "type": "png"},
        )
    except PlaywrightToolError:
        logger.warning("Screenshot failed after %s", tool_name)
        return None
    object_key = rel.replace("\\", "/")
    artifact = await asyncio.to_thread(
        upload_file,
        path,
        bucket=SCREENSHOT_BUCKET,
        object_key=object_key,
        content_type="image/png",
        kind="screenshot",
        thread_id=ctx.thread_id,
        step=ctx.step,
    )
    if artifact:
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            pass
        evidence_context.record_artifact(artifact)
    return artifact


async def capture_console(client: PlaywrightMCP) -> str:
    """Pull, redact, and insert console messages into run_logs. Returns a short summary."""
    ctx = evidence_context.current()
    if ctx is None or ctx.console_pulled:
        return ""
    ctx.console_pulled = True
    try:
        raw = await client.call_mcp("browser_console_messages", {})
    except PlaywrightToolError:
        logger.warning("browser_console_messages failed", exc_info=True)
        return ""
    text = _cap(_filter_console(redact_text(tool_result_text(raw))))
    if not text.strip():
        return ""
    try:
        await asyncio.to_thread(_run_logger(ctx).console, text[:8000])
    except Exception:
        logger.debug("run_logs console insert skipped", exc_info=True)
    return text if len(text) < 1500 else text[:1500] + "\n...[truncated]"


async def capture_network(client: PlaywrightMCP, *, force: bool = False) -> None:
    """Pull, redact, and insert network failures into run_logs (not object storage)."""
    ctx = evidence_context.current()
    if ctx is None:
        return
    if ctx.network_pulled and not force:
        return
    ctx.network_pulled = True
    try:
        raw = await client.call_mcp("browser_network_requests", {})
    except PlaywrightToolError:
        logger.warning("browser_network_requests failed", exc_info=True)
        return
    text = _cap(_filter_network(redact_text(tool_result_text(raw))))
    if not text.strip():
        return
    try:
        await asyncio.to_thread(_run_logger(ctx).network, text[:8000])
    except Exception:
        logger.debug("run_logs network insert skipped", exc_info=True)


async def after_tool(
    client: PlaywrightMCP,
    tool_name: str,
    result: Any,
) -> None:
    """Post-success harness hooks for important browser tools."""
    if tool_name not in _SCREENSHOT_AFTER and tool_name not in _NETWORK_AFTER:
        return
    if _tool_failed(result):
        return
    text = tool_result_text(result)
    if looks_like_login_page(text):
        return

    if tool_name in _SCREENSHOT_AFTER:
        await capture_screenshot(client, tool_name=tool_name)

    if tool_name in _NETWORK_AFTER:
        await capture_network(client)

    if tool_name == "browser_click" and re.search(
        r"(?i)\b(submit|sign[\s-]?in|log[\s-]?in|place order|checkout|send)\b",
        text,
    ):
        await capture_network(client, force=True)
