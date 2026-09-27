import operator
from typing import Annotated, NotRequired, Optional

from typing_extensions import TypedDict

from utils.objects import Plan, StepFindings


class InsightGraphState(TypedDict):
    """State of the InsightGraph"""
    bug_description: str
    url: str
    expected_behavior: NotRequired[Optional[str]]
    plan: NotRequired[Plan]
    current_step: NotRequired[int]
    max_tools_turns: NotRequired[int]
    tools_turns: NotRequired[int]
    error: NotRequired[Optional[str]]
    step_findings: NotRequired[Annotated[list[StepFindings], operator.add]]
    completed: NotRequired[bool]


class ExecutionState(TypedDict):
    """State of the Execution"""
    plan: Plan
    bug_description: str
    url: str
    current_step: int
    max_tools_turns: int
    tools_turns: int
    error: Optional[str]
    step_findings: Annotated[list[StepFindings], operator.add]
    completed: bool
