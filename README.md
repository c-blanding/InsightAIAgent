# Insight AI Agent

Insight AI Agent is a LangGraph application that takes a URL and a bug description, plans how to reproduce the issue in a real browser, runs that plan through Playwright MCP, and produces a structured QA report. Work stays in the browser: navigation, snapshots, clicks, typing, console, and network—no code-change recommendations in the execution path.

## Pipeline

The main graph **`insightaiagent`** runs three stages in order:

1. **Create plan** — LLM outputs a structured `Plan`: ordered steps that are browser interactions only (aligned with Playwright MCP tools).
2. **Execute plan** — A tool-using agent connects to Playwright MCP, opens the URL, works through plan steps, and returns `StepFindings` per step. The standalone `execute_plan` graph can loop until all steps are done.
3. **Finalize report** — LLM synthesizes `step_findings` (and optional `expected_behavior`) into a `Report`.

```text
url + bug_description
        │
        ▼
   create_plan  ──►  Plan
        │
        ▼
  execute_plan  ──►  StepFindings[]  (Playwright MCP)
        │
        ▼
 finalize_report ──►  Report
```

## Graphs

Registered in `langgraph.json`:

| Graph ID | Module | Use |
|----------|--------|-----|
| `insightaiagent` | `insight/agent/src/insightaiagent/agent.py` | End-to-end plan → execute → report |
| `create_plan` | `insight/agent/src/create_plan.py` | Plan generation only |
| `execute_plan` | `insight/agent/src/execute_plan.py` | Execution with conditional loop until complete |

## Inputs and state

Typical Studio or API input:

```json
{
  "url": "http://localhost:5500/products.html",
  "bug_description": "Searching for mug returns no products",
  "expected_behavior": "Search matches product names case-insensitively",
  "max_tools_turns": 30
}
```

| Field | Role |
|-------|------|
| `url` | Application under test |
| `bug_description` | What is going wrong |
| `expected_behavior` | Optional; used when writing the final report |
| `max_tools_turns` | Budget for tool use during a step (default 30) |

Important state fields: `plan`, `current_step`, `step_findings` (append-only list), `completed`, `error`, `report`.

Domain models live in `insight/agent/src/utils/objects.py`: `Plan`, `Steps`, `StepFindings`, `Report`.

## Repository layout

```text
InsightAIAgent/
├── langgraph.json
├── pyproject.toml
├── .env.example
├── insight/
│   ├── agent/src/
│   │   ├── create_plan.py
│   │   ├── execute_plan.py
│   │   ├── insightaiagent/agent.py
│   │   ├── mcp_clients/playwright.py
│   │   └── utils/          # nodes, prompts, states, edges, model
│   └── test_web/           # LumenShop demo storefront (optional QA target)
└── README.md
```

Playwright integration: `PlaywrightMCP` talks to `http://localhost:8931/mcp`, sanitizes tool schemas for OpenAI, and excludes tools that cannot be bound (e.g. `browser_drop`).

## Requirements

- Python ≥ 3.11  
- OpenAI API key  
- Node.js (for Playwright MCP via `npx`)  
- Optional: LangSmith for tracing  

## Setup

Install dependencies (uv recommended):

```powershell
uv sync
```

Copy environment variables:

```powershell
copy .env.example .env
```

Set at least `OPENAI_API_KEY` in `.env`.

Start Playwright MCP in a separate terminal (headed browser by default):

```powershell
npx @playwright/mcp@latest --port 8931
```

Run the LangGraph dev server:

```powershell
langgraph dev
```

Use Studio or the local API to invoke **`insightaiagent`**. If Studio cannot reach localhost, allow local network access for LangSmith or run `langgraph dev --tunnel`.

## Demo storefront

`insight/test_web` is **LumenShop**, a static multi-page shop (catalog, cart, checkout, login, contact, account) meant for exercising the agent. Serve it while testing:

```powershell
python -m http.server 5500 --directory insight/test_web
```

See `insight/test_web/README.md` for run instructions and sample bug scenarios; `BUGS.md` lists planted defects for scoring runs.

## Development notes

- Default chat model: `gpt-4o` in `insight/agent/src/utils/model.py`.
- Do not add a top-level Python package named `mcp` under the agent source tree; it shadows the official MCP SDK. This repo uses `mcp_clients/`.
- After graph or state shape changes, start a new Studio thread so checkpointed state does not conflict with reducers (e.g. `step_findings` must be appended as lists).

## Package metadata

Python package name: `insightaiagent` (`pyproject.toml`).
