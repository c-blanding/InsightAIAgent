from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

from utils.nodes import create_plan
from utils.states import InsightGraphState

load_dotenv()


builder = StateGraph(InsightGraphState)

builder.add_node("create_plan", create_plan)

builder.add_edge(START, "create_plan")
builder.add_edge("create_plan", END)

graph = builder.compile()