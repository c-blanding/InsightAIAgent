from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from utils.nodes import execute_plan
from utils.states import InsightGraphState

load_dotenv()


builder = StateGraph(InsightGraphState)

builder.add_node("execute_plan", execute_plan)

builder.add_edge(START, "execute_plan")
builder.add_edge("execute_plan", END)

graph = builder.compile()
