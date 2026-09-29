import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from utils.nodes import create_plan
from utils.states import InsightGraphState

load_dotenv()


builder = StateGraph(InsightGraphState)

builder.add_node("create_plan", create_plan)

builder.add_edge(START, "create_plan")
builder.add_edge("create_plan", END)

graph = builder.compile()
