import operator
from typing import Annotated, NotRequired, Optional

from typing_extensions import TypedDict

from insightai.utils.objects import Plan, Report, StepFindings, Timeline


class InsightGraphState(TypedDict):
    """State of the InsightGraph"""
    bug_description: str
    url: str
    auth_profile_id: NotRequired[Optional[str]]
    expected_behavior: NotRequired[Optional[str]]
    plan: NotRequired[Plan]
    current_step: NotRequired[int]
    max_tools_turns: NotRequired[int]
    tools_turns: NotRequired[int]
    error: NotRequired[Optional[str]]
    step_findings: NotRequired[Annotated[list[StepFindings], operator.add]]
    completed: NotRequired[bool]
    report: NotRequired[Report]
    timeline: NotRequired[Timeline]

class CreateGraphState(TypedDict):
    """State of the CreateGraph"""
    bug_description: str
    url: str
    expected_behavior: Optional[str]
    max_tools_turns: int = 30
    auth_profile_id: NotRequired[Optional[str]] = None


class ExecutionState(TypedDict):
    """State of the Execution"""
    plan: Plan
    bug_description: str
    url: str
    auth_profile_id: NotRequired[Optional[str]]
    current_step: int
    max_tools_turns: int
    tools_turns: int
    error: Optional[str]
    step_findings: Annotated[list[StepFindings], operator.add]
    completed: bool
    timeline: Timeline


class ReportState(TypedDict):
    """State of the Report"""
    plan: Plan
    step_findings: list[StepFindings]
    error: Optional[str]
    completed: bool
    bug_description: str
    url: str
    expected_behavior: Optional[str]
    timeline: Timeline