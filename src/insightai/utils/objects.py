from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    """Pointer to an evidence object in Neon Object Storage (no bytes)."""

    kind: str = Field(description="screenshot | video")
    bucket: str = Field(description="Neon bucket name")
    object_key: str = Field(description="Object key within the bucket")


class StepFindings(BaseModel):
    findings: Optional[str] = Field(None, description="Findings of the step")
    page_snapshot: Optional[str] = Field(None, description="Page snapshot of the step")
    error: Optional[str] = Field(None, description="Error of the step")
    network_messages: Optional[List[str]] = Field(
        None, description="Network messages of the step"
    )
    console_messages: Optional[List[str]] = Field(
        None, description="Console messages of the step"
    )
    artifacts: List[ArtifactRef] = Field(
        default_factory=list,
        description="Evidence object keys (screenshots, video). No binaries.",
    )



class Steps(BaseModel):
    step: Optional[int] = Field(None, description="Step number")
    action: Optional[str] = Field(None, description="Action to perform")
    completed: bool = Field(False, description="Whether the step is completed")
    findings: Optional[StepFindings] = Field(None, description="Findings of the step")


class Plan(BaseModel):
    goal: Optional[str] = Field(None, description="Goal of the plan")
    instructions: Optional[List[Steps]] = Field(None, description="Steps to follow")
    steps: Optional[int] = Field(None, description="Total number of steps")
    completed: bool = Field(False, description="Whether the plan is completed")

    @property
    def formatted_plan(self) -> str:
        return "\n".join(
            ["Goal: " + (self.goal or "") + "\n"]
            + [f"{step.step}. {step.action} \n" for step in (self.instructions or [])]
        )


class Report(BaseModel):
    report: Optional[str] = Field(None, description="Report of the bug")

    @property
    def formatted_report(self) -> str:
        return self.report or "No report found"


class TimelineRef(BaseModel):
    """Optional object-storage pointer attached to a timeline event."""

    kind: Optional[str] = None
    bucket: Optional[str] = None
    object_key: Optional[str] = None


class TimelineEvent(BaseModel):
    """One ordered breadcrumb on the run timeline / action log."""

    id: str
    t: Optional[str] = Field(None, description="ISO-8601 timestamp")
    step: Optional[int] = None
    event_type: str
    tool: Optional[str] = None
    title: str
    detail: Optional[str] = None
    status: Optional[str] = "ok"
    ref: Optional[TimelineRef] = None
    source: Optional[str] = Field(
        None, description="run_events | run_artifacts | run_logs"
    )


class TimelineArtifact(BaseModel):
    id: str
    t: Optional[str] = None
    step: Optional[int] = None
    kind: str
    bucket: str
    object_key: str
    content_type: Optional[str] = None
    byte_size: Optional[int] = None


class TimelineLog(BaseModel):
    id: str
    t: Optional[str] = None
    step: Optional[int] = None
    kind: str
    source: Optional[str] = None
    message: str


class TimelineChapter(BaseModel):
    step: Optional[int] = None
    label: str
    events: List[TimelineEvent] = Field(default_factory=list)


class Timeline(BaseModel):
    """Merged timeline view for a QA run thread (events + artifacts + logs)."""

    thread_id: str
    events: List[TimelineEvent] = Field(default_factory=list)
    actions: List[TimelineEvent] = Field(default_factory=list)
    artifacts: List[TimelineArtifact] = Field(default_factory=list)
    logs: List[TimelineLog] = Field(default_factory=list)
    chapters: List[TimelineChapter] = Field(default_factory=list)
    error: Optional[str] = None

    @property
    def formatted_timeline(self) -> str:
        return "\n".join([chapter.label for chapter in self.chapters])
    @property
    def formatted_events(self) -> str:
        return "\n".join([event.title for event in self.events])
    @property
    def formatted_actions(self) -> str:
        return "\n".join([action.title for action in self.actions])
    @property
    def formatted_artifacts(self) -> str:
        return "\n".join([artifact.object_key for artifact in self.artifacts])
    @property
    def formatted_logs(self) -> str:
        return "\n".join([log.message for log in self.logs])



class Run(BaseModel):
    """Persisted summary of one QA run (graph inputs + outcomes + timeline)."""

    thread_id: str
    bug_description: str
    url: str
    expected_behavior: Optional[str] = None
    plan: Optional[Plan] = None
    step_findings: List[StepFindings] = Field(default_factory=list)
    timeline: Optional[Timeline] = None
    report: Optional[Report] = None
    error: Optional[str] = None
    completed: bool = False
    id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Run":
        return cls(
            id=str(row.id) if getattr(row, "id", None) is not None else None,
            thread_id=row.thread_id,
            bug_description=row.bug_description,
            url=row.url,
            expected_behavior=row.expected_behavior,
            plan=Plan.model_validate(row.plan) if row.plan else None,
            step_findings=[
                StepFindings.model_validate(item) for item in (row.step_findings or [])
            ],
            timeline=Timeline.model_validate(row.timeline) if row.timeline else None,
            report=Report.model_validate(row.report) if row.report else None,
            error=row.error,
            completed=bool(row.completed),
            created_at=row.created_at.isoformat() if row.created_at else None,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )
