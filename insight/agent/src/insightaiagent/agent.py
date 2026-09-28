from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from utils.nodes import create_plan, execute_plan
from utils.states import InsightGraphState

load_dotenv()

builder = StateGraph(InsightGraphState)
builder.add_node("create_plan", create_plan)
builder.add_node("execute_plan", execute_plan)
builder.add_edge(START, "create_plan")
builder.add_edge("create_plan", "execute_plan")
builder.add_edge("execute_plan", END)

graph = builder.compile()
