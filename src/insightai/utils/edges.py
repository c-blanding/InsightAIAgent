from typing import Any, Literal

from langgraph.graph import END

from insightai.utils.states import ExecutionState, InsightGraphState

AUTH_EXPIRED = "auth_expired"


def is_auth_expired(state: InsightGraphState | ExecutionState | dict[str, Any]) -> bool:
    return (state.get("error") or "") == AUTH_EXPIRED


def after_execute_step(
    state: ExecutionState,
) -> Literal["wait_for_login", "execute_plan", "done"]:
    """Route after one execute step: login wall, next step, or finished."""
    if is_auth_expired(state):
        return "wait_for_login"
    if state.get("completed") or state.get("error"):
        return "done"
    return "execute_plan"


def execute_plan_edge(state: ExecutionState) -> Literal["wait_for_login", "execute_plan", END]:
    route = after_execute_step(state)
    if route == "done":
        return END
    return route


def insight_after_execute(
    state: InsightGraphState,
) -> Literal["wait_for_login", "execute_plan", "finalize_report"]:
    route = after_execute_step(state)  # type: ignore[arg-type]
    if route == "done":
        return "finalize_report"
    return route
