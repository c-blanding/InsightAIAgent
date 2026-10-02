"""LangGraph Studio / ``langgraph.json`` entry for the end-to-end Insight pipeline.

Application code should use ``InsightAgent`` from ``src/insightai/agent.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).parent.parent  # src/insightai
if not _PKG.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from dotenv import load_dotenv

from agent import build_graph

load_dotenv()

# Platform (langgraph dev / Studio) supplies its own checkpointer.
graph = build_graph()
