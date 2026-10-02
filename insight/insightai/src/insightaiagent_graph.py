"""LangGraph Studio / ``langgraph.json`` entry for the end-to-end Insight pipeline.

Application code should use ``InsightAgent`` from ``insightaiagent`` instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent
if not _SRC.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

from insightaiagent.agent import build_graph

load_dotenv()

# Platform (langgraph dev / Studio) supplies its own checkpointer.
graph = build_graph()
