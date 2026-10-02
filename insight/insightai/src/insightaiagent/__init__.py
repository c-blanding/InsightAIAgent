"""Insight AI Agent public API."""

from .agent import (
    InsightAgent,
    InsightAgentError,
    InsightInputError,
    InsightOutcome,
    InsightPreflightError,
    InsightResult,
    NeedsLogin,
    build_graph,
)

__all__ = [
    "InsightAgent",
    "InsightAgentError",
    "InsightInputError",
    "InsightOutcome",
    "InsightPreflightError",
    "InsightResult",
    "NeedsLogin",
    "build_graph",
    "main",
]


def main() -> None:
    print(
        "Insight AI Agent — use InsightAgent().run({...}) or "
        "`langgraph dev` for Studio."
    )
