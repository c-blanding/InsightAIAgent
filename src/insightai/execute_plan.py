from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from insightai.utils.edges import execute_plan_edge
from insightai.utils.nodes import execute_plan, wait_for_login
from insightai.utils.states import InsightGraphState

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
