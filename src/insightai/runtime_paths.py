"""Writable runtime dirs and absolute paths without calling ``os.getcwd``.

LangGraph's blockbuster treats ``os.getcwd`` (used by ``Path.resolve()`` even for
absolute paths on Windows) as a blocking call on the event loop. Build paths from
absolute ``__file__`` / env overrides and normalize with ``os.path.normpath``.

All Playwright MCP snapshots, video, traces, and auth scratch belong under
``.playwright-mcp`` (or ``INSIGHT_MEDIA_DIR``) — never the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/insightai/runtime_paths.py -> repo root is parents[2]
_SRC_FILE = Path(__file__)
if not _SRC_FILE.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
REPO_ROOT = _SRC_FILE.parents[2]

# Playwright MCP's conventional output folder; keep agent + MCP aligned.
_DEFAULT_MEDIA = REPO_ROOT / ".playwright-mcp"


def absolute_path(path: Path | str) -> Path:
    """Normalize an already-absolute path without ``Path.resolve()`` / ``getcwd``."""
    p = Path(path)
    if not p.is_absolute():
        raise ValueError(f"expected absolute path, got {p}")
    return Path(os.path.normpath(str(p)))


def _env_dir(name: str, default: Path) -> Path:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    p = Path(raw)
    if not p.is_absolute():
        # Relative overrides are repo-rooted, never cwd-relative.
        p = REPO_ROOT / p
    return absolute_path(p)


def media_dir() -> Path:
    """Harness + MCP staging. Must match Playwright MCP ``--output-dir``."""
    return _env_dir("INSIGHT_MEDIA_DIR", _DEFAULT_MEDIA)


def auth_scratch_dir() -> Path:
    """Short-lived storage-state JSON for Playwright restore/export."""
    return _env_dir("INSIGHT_AUTH_SCRATCH_DIR", media_dir() / "auth")


def playwright_mcp_url() -> str:
    return (
        os.environ.get("PLAYWRIGHT_MCP_URL") or "http://localhost:8931/mcp"
    ).strip() or "http://localhost:8931/mcp"
