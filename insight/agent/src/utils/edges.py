from typing import Literal
from langgraph.graph import END
from utils.states import ExecutionState


def execute_plan_edge(state: ExecutionState) -> Literal["execute_plan", END]:
    if state["completed"]:
        return END

    return "execute_plan"
