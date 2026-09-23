from typing import List

from pydantic import BaseModel, Field


class Steps(BaseModel):
    step: int = Field(None, description="Step number")
    action: str = Field(None, description="Action to perform")
    completed: bool = Field(False, description="Whether the step is completed")


class Plan(BaseModel):
    goal: str = Field(None, description="Goal of the plan")
    instructions: List[Steps] = Field(None, description="Steps to follow")
    steps: int = Field(None, description="Total number of steps")
    completed: bool = Field(False, description="Whether the plan is completed")


