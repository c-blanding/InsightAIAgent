"""Legacy alias package — prefer ``import insightai`` / ``from insightai import InsightAgent``."""

from insightai import (
    InsightAgent,
    InsightAgentError,
    InsightInputError,
    InsightOutcome,
    InsightPreflightError,
    InsightResult,
    NeedsLogin,
    build_graph,
    main,
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
