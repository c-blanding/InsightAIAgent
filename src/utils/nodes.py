import json

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage

from mcp_clients.playwright import PlaywrightMCP
from utils.model import llm
from utils.objects import Plan, StepFindings
from utils.prompts import execute_plan_prompt, plan_intruction_prompt
from utils.states import ExecutionState, InsightGraphState


def create_plan(state: InsightGraphState):
    url = state["url"]
    bug = state["bug_description"]

    system_message = plan_intruction_prompt.format(url=url, bug=bug)

    plan_llm = llm.with_structured_output(Plan)

    plan = plan_llm.invoke(
        [SystemMessage(content=system_message)]
        + [HumanMessage(content="Please give me a plan to find this bug")]
    )

    return {"plan": plan, "current_step": 1, "completed": False, "step_findings": []}


async def execute_plan(state: ExecutionState) -> InsightGraphState:
    """Run one plan step via Playwright MCP tools."""
    plan = state["plan"]
    if plan is None:
        return {
            "step_findings": [
                StepFindings(findings="", error="No plan provided. Run create_plan first.")
            ],
            "completed": False,
            "error": "missing_plan",
        }

    url = state["url"]
    bug = state["bug_description"]
    max_turns = state.get("max_tools_turns") or 30
    current_step = state["current_step"]
    step = plan.instructions[current_step - 1]

    if step.completed:
        return {
            "step_findings": [step.findings] if step.findings else [],
            "completed": True,
            "error": None,
            "tools_turns": 0,
        }

    system_prompt = execute_plan_prompt.format(
        url=url,
        bug=bug,
        plan=plan.formatted_plan,
        current_step=current_step,
    )

    try:
        async with PlaywrightMCP() as playwright:
            agent = create_agent(
                llm,
                playwright.tools,
                system_prompt=system_prompt,
                response_format=StepFindings,
            )
            result = await agent.ainvoke(
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                f"Execute only step {current_step}: {step.action}. "
                                "Use browser tools as needed, then return findings."
                            )
                        )
                    ]
                },
                config={"recursion_limit": max_turns * 2},
            )
    except ConnectionError as exc:
        return {
            "step_findings": [StepFindings(findings="", error=str(exc))],
            "completed": False,
            "error": "mcp_unreachable",
        }

    findings = result.get("structured_response")
    if findings is None:
        content = result.get("messages", [])[-1].content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
                findings = StepFindings(**payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                findings = StepFindings(
                    findings=str(content), error="missing_structured_response"
                )
        else:
            findings = StepFindings(
                findings=str(content), error="missing_structured_response"
            )

    plan.instructions[current_step - 1].completed = True
    plan.instructions[current_step - 1].findings = findings
    plan.completed = all(s.completed for s in plan.instructions)

    updates: InsightGraphState = {
        "step_findings": [findings],
        "plan": plan,
        "completed": plan.completed,
        "error": findings.error,
    }
    if not plan.completed and not findings.error:
        updates["current_step"] = current_step + 1
    return updates
