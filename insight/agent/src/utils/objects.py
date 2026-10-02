from typing import List, Optional

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
