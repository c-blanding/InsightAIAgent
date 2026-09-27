from typing import List

from pydantic import BaseModel, Field

class StepFindings(BaseModel):
    findings: str = Field(None, description="Findings of the step")
    page_snapshot: str = Field(None, description="Page snapshot of the step")
    error: str = Field(None, description="Error of the step")
    console_messages: List[str] = Field(None, description="Console messages of the step")

class Steps(BaseModel):
    step: int = Field(None, description="Step number")
    action: str = Field(None, description="Action to perform")
    completed: bool = Field(False, description="Whether the step is completed")
    findings: StepFindings = Field(None, description="Findings of the step")  # pyright: ignore[reportUndefinedVariable]


class Plan(BaseModel):
    goal: str = Field(None, description="Goal of the plan")
    instructions: List[Steps] = Field(None, description="Steps to follow")
    steps: int = Field(None, description="Total number of steps")
    completed: bool = Field(False, description="Whether the plan is completed")


    @property
    def formatted_plan(self) -> str:
        return "\n".join(["Goal: " + self.goal + "\n"] + [f"{step.step}. {step.action} \n" for step in self.instructions])

    




    

