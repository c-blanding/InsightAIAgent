import sys
from pathlib import Path

# agent.py lives in insightaiagent/; put sibling packages (utils, …) on path
_SRC_ROOT = Path(__file__).parent.parent
if not _SRC_ROOT.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from utils.edges import insight_after_execute
from utils.nodes import create_plan, execute_plan, finalize_report, wait_for_login
from utils.states import InsightGraphState

load_dotenv()

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

graph = builder.compile()
