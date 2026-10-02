"""Insight AI Agent — LangGraph pipeline + programmatic API.

Studio / ``langgraph.json`` use ``insightaiagent_graph.py``.
Application code should use ``InsightAgent``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

# agent.py lives in src/insightai/; put src/insightai/src on path for utils/db/…
_SRC_ROOT = Path(__file__).parent / "src"
if not _SRC_ROOT.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from evidence.session import normalize_thread_id, release
from runtime_paths import playwright_mcp_url
from utils.edges import insight_after_execute
from utils.nodes import create_plan, execute_plan, finalize_report, wait_for_login
from utils.objects import Plan, Report, StepFindings, Timeline
from utils.states import InsightGraphState

load_dotenv()

logger = logging.getLogger(__name__)

_FORBIDDEN_STATE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "cookies",
        "cookie",
        "authorization",
        "storage_state",
        "storageState",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
    }
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|cookie|authorization|storage.?state|"
    r"access.?token|refresh.?token|api.?key)"
)
_DEFAULT_MAX_TOOLS_TURNS = 30
_MAX_TOOLS_TURNS_CAP = 200


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NeedsLogin:
    """Graph paused at a login wall; sign in in the browser, then ``acontinue``."""

    thread_id: str
    message: str
    login_url: str
    origin: str
    auth_profile_id: str
    interrupt_id: Optional[str] = None
    raw: Any = None


@dataclass
class InsightResult:
    """Final (or stopped) outcome of an Insight run."""

    thread_id: str
    url: str
    bug_description: str
    report: Optional[Report] = None
    plan: Optional[Plan] = None
    step_findings: list[StepFindings] = field(default_factory=list)
    timeline: Optional[Timeline] = None
    expected_behavior: Optional[str] = None
    auth_profile_id: Optional[str] = None
    error: Optional[str] = None
    completed: bool = False
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.report is not None and not self.error


InsightOutcome = Union[InsightResult, NeedsLogin]


class InsightAgentError(Exception):
    """Base error for InsightAgent validation / preflight failures."""


class InsightInputError(InsightAgentError, ValueError):
    """Caller passed invalid or unsafe graph input."""


class InsightPreflightError(InsightAgentError, RuntimeError):
    """Required runtime dependency is missing or unreachable."""


# ---------------------------------------------------------------------------
# Graph factory (Studio entry: insightaiagent_graph.py; class adds a checkpointer)
# ---------------------------------------------------------------------------


def build_graph(*, checkpointer: BaseCheckpointSaver | None = None):
    """Compile the plan → execute → report graph."""
    builder = StateGraph(InsightGraphState)
    builder.add_node("create_plan", create_plan)
    builder.add_node("execute_plan", execute_plan)
    builder.add_node("wait_for_login", wait_for_login)
    builder.add_node("finalize_report", finalize_report)
    builder.add_edge(START, "create_plan")
    builder.add_edge("create_plan", "execute_plan")
    builder.add_conditional_edges(
        "execute_plan",
        insight_after_execute,
        ["wait_for_login", "execute_plan", "finalize_report"],
    )
    builder.add_edge("wait_for_login", "execute_plan")
    builder.add_edge("finalize_report", END)
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# InsightAgent
# ---------------------------------------------------------------------------


class InsightAgent:
    """Programmatic entry point for the Insight QA pipeline.

    Example::

        agent = InsightAgent()
        outcome = await agent.arun({
            "url": "http://localhost:5500/products.html",
            "bug_description": "Searching for mug returns no products",
        })
        if isinstance(outcome, NeedsLogin):
            # Sign in in the headed browser, then:
            outcome = await agent.acontinue(outcome.thread_id)
        print(outcome.report)
    """

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        mcp_url: str | None = None,
        require_database: bool = True,
        preflight: bool = True,
    ) -> None:
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self._graph = build_graph(checkpointer=self._checkpointer)
        self._mcp_url = (mcp_url or playwright_mcp_url()).rstrip("/")
        self._require_database = require_database
        if preflight:
            self.preflight(check_mcp=False)

    # -- config / validation -------------------------------------------------

    def preflight(self, *, check_mcp: bool = True) -> None:
        """Fail fast if required env / services are missing."""
        missing: list[str] = []
        if not (os.environ.get("OPENAI_API_KEY") or "").strip():
            missing.append("OPENAI_API_KEY")
        if self._require_database and not (
            os.environ.get("DATABASE_URL") or ""
        ).strip():
            missing.append("DATABASE_URL")
        if missing:
            raise InsightPreflightError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        if check_mcp:
            self._check_mcp()

    def _check_mcp(self) -> None:
        url = self._mcp_url
        try:
            # Playwright MCP speaks Streamable HTTP; any HTTP response
            # (including 4xx) means the process is listening.
            with urlopen(url, timeout=3) as resp:  # noqa: S310 — configured MCP URL
                _ = resp.status
        except HTTPError:
            return
        except (URLError, TimeoutError, OSError) as exc:
            raise InsightPreflightError(
                f"Playwright MCP unreachable at {url}. "
                "Start it with scripts/start-playwright-mcp.bat "
                "or set PLAYWRIGHT_MCP_URL."
            ) from exc

    def validate_input(self, state: dict[str, Any]) -> InsightGraphState:
        """Normalize and validate graph input; reject credential-bearing keys."""
        if not isinstance(state, dict):
            raise InsightInputError("state must be a dict")

        for key in state:
            if key in _FORBIDDEN_STATE_KEYS or _FORBIDDEN_KEY_RE.search(str(key)):
                raise InsightInputError(
                    f"Refusing state key {key!r}: passwords, cookies, and "
                    "storage-state must not enter graph input (they are checkpointed)."
                )

        url = (state.get("url") or "").strip() if isinstance(state.get("url"), str) else ""
        bug = (
            (state.get("bug_description") or "").strip()
            if isinstance(state.get("bug_description"), str)
            else ""
        )
        if not url:
            raise InsightInputError("url is required")
        if not bug:
            raise InsightInputError("bug_description is required")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InsightInputError(
                f"url must be an absolute http(s) URL, got {url!r}"
            )

        max_turns = state.get("max_tools_turns", _DEFAULT_MAX_TOOLS_TURNS)
        try:
            max_turns_i = int(max_turns)
        except (TypeError, ValueError) as exc:
            raise InsightInputError("max_tools_turns must be an integer") from exc
        if max_turns_i < 1:
            raise InsightInputError("max_tools_turns must be >= 1")
        max_turns_i = min(max_turns_i, _MAX_TOOLS_TURNS_CAP)

        expected = state.get("expected_behavior")
        if expected is not None and not isinstance(expected, str):
            raise InsightInputError("expected_behavior must be a string or omitted")
        expected_s = expected.strip() if isinstance(expected, str) else None
        if expected_s == "":
            expected_s = None

        auth = state.get("auth_profile_id")
        if auth is not None and not isinstance(auth, str):
            raise InsightInputError("auth_profile_id must be a string or omitted")
        auth_s = auth.strip() if isinstance(auth, str) else None
        if auth_s == "":
            auth_s = None
        if auth_s and not (os.environ.get("AUTH_DATA_KEY") or "").strip():
            raise InsightInputError(
                "auth_profile_id is set but AUTH_DATA_KEY is missing"
            )

        cleaned: InsightGraphState = {
            "url": url,
            "bug_description": bug,
            "max_tools_turns": max_turns_i,
        }
        if expected_s is not None:
            cleaned["expected_behavior"] = expected_s
        if auth_s is not None:
            cleaned["auth_profile_id"] = auth_s
        return cleaned

    def _config(
        self,
        thread_id: str | None,
        *,
        extra: RunnableConfig | None = None,
    ) -> tuple[str, RunnableConfig]:
        tid = normalize_thread_id(thread_id) if thread_id else normalize_thread_id(
            str(uuid.uuid4())
        )
        config: RunnableConfig = {"configurable": {"thread_id": tid}}
        if extra:
            merged = dict(extra)
            configurable = dict(merged.get("configurable") or {})
            configurable["thread_id"] = tid
            merged["configurable"] = configurable
            config = merged  # type: ignore[assignment]
        return tid, config

    # -- run / resume --------------------------------------------------------

    async def arun(
        self,
        state: dict[str, Any],
        *,
        thread_id: str | None = None,
        config: RunnableConfig | None = None,
        timeout: float | None = None,
        check_mcp: bool = True,
    ) -> InsightOutcome:
        """Run the full pipeline. Returns ``InsightResult`` or ``NeedsLogin``."""
        cleaned = self.validate_input(state)
        if check_mcp:
            await asyncio.to_thread(self._check_mcp)
        tid, run_config = self._config(thread_id, extra=config)
        return await self._ainvoke(cleaned, tid, run_config, timeout=timeout)

    async def acontinue(
        self,
        thread_id: str,
        *,
        resume: Any = None,
        config: RunnableConfig | None = None,
        timeout: float | None = None,
        cancel: bool = False,
    ) -> InsightOutcome:
        """Resume after a login wall.

        Default resume confirms Continue (empty / ``{confirmed: true}``).
        Pass ``cancel=True`` to abort the sign-in pause.
        Never put a password in ``resume``.
        """
        tid = normalize_thread_id(thread_id)
        if not tid or tid == "local":
            raise InsightInputError("thread_id is required to continue a run")
        if resume is not None and isinstance(resume, dict):
            for key in resume:
                if key in _FORBIDDEN_STATE_KEYS or _FORBIDDEN_KEY_RE.search(str(key)):
                    raise InsightInputError(
                        f"Refusing resume key {key!r}: do not send credentials "
                        "in the Continue payload"
                    )
        payload: Any
        if cancel:
            payload = {"cancelled": True}
        elif resume is None:
            payload = {"confirmed": True}
        else:
            payload = resume

        _, run_config = self._config(tid, extra=config)
        return await self._ainvoke(
            Command(resume=payload),
            tid,
            run_config,
            timeout=timeout,
        )

    def run(self, state: dict[str, Any], **kwargs: Any) -> InsightOutcome:
        """Sync wrapper around ``arun`` (not for use inside a running event loop)."""
        return asyncio.run(self.arun(state, **kwargs))

    def continue_run(self, thread_id: str, **kwargs: Any) -> InsightOutcome:
        """Sync wrapper around ``acontinue``."""
        return asyncio.run(self.acontinue(thread_id, **kwargs))

    async def astream(
        self,
        state: dict[str, Any],
        *,
        thread_id: str | None = None,
        config: RunnableConfig | None = None,
        check_mcp: bool = True,
        stream_mode: Literal["updates", "values"] = "updates",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield graph progress chunks (node updates by default)."""
        cleaned = self.validate_input(state)
        if check_mcp:
            await asyncio.to_thread(self._check_mcp)
        tid, run_config = self._config(thread_id, extra=config)
        try:
            async for chunk in self._graph.astream(
                cleaned, run_config, stream_mode=stream_mode
            ):
                yield {"thread_id": tid, "chunk": chunk}
        except BaseException:
            await self._cleanup(tid)
            raise

    # -- helpers -------------------------------------------------------------

    def get_timeline(self, thread_id: str) -> Timeline:
        """Assemble the persisted timeline for a finished (or in-progress) thread."""
        from evidence.timeline import assemble_timeline

        return assemble_timeline(normalize_thread_id(thread_id))

    def get_run(self, thread_id: str) -> Any:
        """Load the persisted ``runs`` row for ``thread_id``, or ``None``."""
        from db import get_database

        return get_database().get_run(normalize_thread_id(thread_id))

    async def _ainvoke(
        self,
        payload: Any,
        thread_id: str,
        config: RunnableConfig,
        *,
        timeout: float | None,
    ) -> InsightOutcome:
        try:
            coro = self._graph.ainvoke(payload, config, version="v2")
            if timeout is not None:
                output = await asyncio.wait_for(coro, timeout=timeout)
            else:
                output = await coro
        except asyncio.TimeoutError:
            await self._cleanup(thread_id)
            raise InsightAgentError(
                f"Run timed out after {timeout}s (thread_id={thread_id})"
            )
        except BaseException:
            await self._cleanup(thread_id)
            raise

        interrupts = getattr(output, "interrupts", ()) or ()
        value = getattr(output, "value", output)
        if not isinstance(value, dict):
            value = dict(value) if value is not None else {}

        if interrupts:
            return self._needs_login(thread_id, interrupts[0])

        # Backward-compat if version=v2 is unavailable somehow
        if isinstance(value, dict) and value.get("__interrupt__"):
            raw = value["__interrupt__"]
            first = raw[0] if raw else None
            if first is not None:
                return self._needs_login(thread_id, first)

        return self._to_result(thread_id, value)

    def _needs_login(self, thread_id: str, interrupt_obj: Any) -> NeedsLogin:
        raw = getattr(interrupt_obj, "value", interrupt_obj)
        data = raw if isinstance(raw, dict) else {}
        return NeedsLogin(
            thread_id=thread_id,
            message=str(
                data.get("message")
                or "Sign in in the headed browser, then continue."
            ),
            login_url=str(data.get("login_url") or ""),
            origin=str(data.get("origin") or ""),
            auth_profile_id=str(data.get("auth_profile_id") or ""),
            interrupt_id=getattr(interrupt_obj, "id", None),
            raw=raw,
        )

    def _to_result(self, thread_id: str, state: dict[str, Any]) -> InsightResult:
        plan = state.get("plan")
        report = state.get("report")
        findings = state.get("step_findings") or []
        timeline = state.get("timeline")

        # Prefer assembled timeline from DB when not already on state
        if timeline is None:
            try:
                timeline = self.get_timeline(thread_id)
                if timeline.error and not timeline.events:
                    timeline = None
            except Exception:
                logger.debug("timeline assemble skipped", exc_info=True)
                timeline = None

        return InsightResult(
            thread_id=thread_id,
            url=str(state.get("url") or ""),
            bug_description=str(state.get("bug_description") or ""),
            report=report if isinstance(report, Report) else (
                Report.model_validate(report) if report else None
            ),
            plan=plan if isinstance(plan, Plan) else (
                Plan.model_validate(plan) if plan else None
            ),
            step_findings=[
                item
                if isinstance(item, StepFindings)
                else StepFindings.model_validate(item)
                for item in findings
            ],
            timeline=timeline
            if timeline is None or isinstance(timeline, Timeline)
            else Timeline.model_validate(timeline),
            expected_behavior=state.get("expected_behavior"),
            auth_profile_id=state.get("auth_profile_id"),
            error=state.get("error"),
            completed=bool(state.get("completed")) or report is not None,
            state=dict(state),
        )

    async def _cleanup(self, thread_id: str) -> None:
        try:
            await release(thread_id, finished=True)
        except Exception:
            logger.warning(
                "Failed to release Playwright session for %s",
                thread_id,
                exc_info=True,
            )
