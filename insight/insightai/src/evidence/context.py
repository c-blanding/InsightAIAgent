"""Per-step evidence context shared by tool wrappers and execute_plan."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepEvidenceContext:
    thread_id: str
    step: int
    skip_screenshots: bool = False
    seq: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    network_pulled: bool = False
    console_pulled: bool = False


_current: ContextVar[StepEvidenceContext | None] = ContextVar(
    "evidence_step_context", default=None
)


def begin_step(
    thread_id: str,
    step: int,
    *,
    skip_screenshots: bool = False,
) -> StepEvidenceContext:
    ctx = StepEvidenceContext(
        thread_id=thread_id,
        step=step,
        skip_screenshots=skip_screenshots,
    )
    _current.set(ctx)
    return ctx


def current() -> StepEvidenceContext | None:
    return _current.get()


def next_seq() -> int:
    ctx = _current.get()
    if ctx is None:
        return 0
    ctx.seq += 1
    return ctx.seq


def record_artifact(artifact: dict[str, Any] | None) -> None:
    if not artifact:
        return
    ctx = _current.get()
    if ctx is None:
        return
    ctx.artifacts.append(
        {
            "kind": artifact["kind"],
            "bucket": artifact["bucket"],
            "object_key": artifact["object_key"],
        }
    )


def take_artifacts() -> list[dict[str, Any]]:
    ctx = _current.get()
    if ctx is None:
        return []
    items = list(ctx.artifacts)
    ctx.artifacts.clear()
    return items


def clear_step() -> None:
    _current.set(None)
