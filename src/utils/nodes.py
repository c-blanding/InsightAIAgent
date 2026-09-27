from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_protocol.protocol import AgentStatusEntry

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

    return {"plan": plan, "current_step": 1, "completed": False}





async def execute_plan(state: ExecutionState) -> InsightGraphState:
    """Run the full plan in one browser session via Playwright MCP tools."""
    plan = state["plan"]
    if plan is None:
        return {
            "findings": "No plan provided. Run create_plan first.",
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
            "findings": step.findings,
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
            # Tools go on create_agent — not on ainvoke / with_structured_output.
            # response_format asks for StepFindings after the tool loop finishes.
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
        return {"findings": StepFindings(findings="", error=str(exc)), "completed": False, "error": "mcp_unreachable"}

    findings = result.get("structured_response")

    if findings is None:
        messages = result.get("messages", [])
        final = messages[-1].content if messages else "No findings returned."
        findings = StepFindings(findings=str(final), error="missing_structured_response")
        plan.instructions[current_step - 1].completed = True
        plan.instructions[current_step - 1].findings = findings
        plan.completed = all(step.completed for step in plan.instructions)
        return {"findings": [findings], "plan": plan, "completed": plan.completed, "error": None}
    
    findings = StepFindings(findings=findings, error=None)
    plan.instructions[current_step - 1].completed = True
    plan.instructions[current_step - 1].findings = findings
    plan.completed = all(step.completed for step in plan.instructions)
    if current_step.completed:
       current_step = current_step + 1
    
    return {"findings": [findings], "plan": plan, "completed": plan.completed, "error": None, "current_step": current_step}