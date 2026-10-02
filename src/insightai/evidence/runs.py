"""Persist completed QA runs (plan, findings, timeline snapshot) to Postgres."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from insightai.auth.redact import redact_secrets
from insightai.evidence.session import thread_id_from_config
from insightai.evidence.timeline import assemble_timeline
from insightai.utils.objects import Plan, Report, Run, StepFindings, Timeline

logger = logging.getLogger(__name__)


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return redact_secrets(value.model_dump(mode="json"))
    return redact_secrets(value)


def save_run(
    *,
    thread_id: str,
    bug_description: str,
    url: str,
    expected_behavior: str | None = None,
    plan: Plan | dict[str, Any] | None = None,
    step_findings: list[StepFindings] | list[dict[str, Any]] | None = None,
    timeline: Timeline | dict[str, Any] | None = None,
    report: Report | dict[str, Any] | None = None,
    error: str | None = None,
    completed: bool = True,
) -> Run | None:
    """Upsert a run row. Best-effort: returns ``None`` when the DB is unavailable."""
    key = (thread_id or "").strip() or "local"
    try:
        from insightai.db import get_database

        db = get_database()
        db.create_tables()
    except Exception:
        logger.warning("save_run skipped; database unavailable", exc_info=True)
        return None

    if timeline is None:
        timeline = assemble_timeline(key)

    findings_payload = [_dump(item) for item in (step_findings or [])]
    try:
        row = db.upsert_run(
            thread_id=key,
            bug_description=bug_description,
            url=url,
            expected_behavior=expected_behavior,
            plan=_dump(plan),
            step_findings=findings_payload,
            timeline=_dump(timeline),
            report=_dump(report),
            error=error,
            completed=completed,
        )
        return Run.from_row(row)
    except Exception:
        logger.warning("upsert_run failed for thread %s", key, exc_info=True)
        return None


def save_run_from_state(
    state: dict[str, Any],
    *,
    config: RunnableConfig | None = None,
    report: Report | dict[str, Any] | None = None,
    completed: bool | None = None,
) -> Run | None:
    """Build and persist a run from LangGraph state (+ optional report)."""
    thread_id = thread_id_from_config(config)
    done = state.get("completed") if completed is None else completed
    return save_run(
        thread_id=thread_id,
        bug_description=state.get("bug_description") or "",
        url=state.get("url") or "",
        expected_behavior=state.get("expected_behavior"),
        plan=state.get("plan"),
        step_findings=state.get("step_findings") or [],
        report=report if report is not None else state.get("report"),
        error=state.get("error"),
        completed=bool(done) if done is not None else True,
    )
