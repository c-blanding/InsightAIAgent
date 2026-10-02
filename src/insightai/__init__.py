"""Insight AI Agent — plan, execute, and report browser QA runs."""

from insightai.agent import (
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
        "Insight AI Agent — use InsightAgent().arun({...}) or "
        "`langgraph dev` for Studio."
    )
