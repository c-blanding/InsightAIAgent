import sys
from pathlib import Path

_SRC = Path(__file__).parent
if not _SRC.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from utils.edges import execute_plan_edge
from utils.nodes import execute_plan, wait_for_login
from utils.states import InsightGraphState

load_dotenv()


builder = StateGraph(InsightGraphState)

builder.add_node("execute_plan", execute_plan)
builder.add_node("wait_for_login", wait_for_login)

builder.add_edge(START, "execute_plan")
builder.add_conditional_edges(
    "execute_plan",
    execute_plan_edge,
    ["wait_for_login", "execute_plan", END],
)
builder.add_edge("wait_for_login", "execute_plan")

graph = builder.compile()
