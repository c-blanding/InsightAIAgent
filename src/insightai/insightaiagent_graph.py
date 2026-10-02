"""LangGraph Studio / ``langgraph.json`` entry for the end-to-end Insight pipeline.

Application code should use ``from insightai import InsightAgent``.
"""

from __future__ import annotations

from dotenv import load_dotenv

from insightai.agent import build_graph

load_dotenv()

# Platform (langgraph dev / Studio) supplies its own checkpointer.
graph = build_graph()
