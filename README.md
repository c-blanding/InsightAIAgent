# Insight AI Agent

A LangGraph-based QA agent that turns a bug report + URL into a browser-only reproduction plan, then drives a real Chromium session through [Playwright MCP](https://playwright.dev/docs/getting-started-mcp) to attempt that plan.

The agent stays in the browser: it navigates, snapshots the accessibility tree, clicks/types, and reports what it observed. It does not propose code fixes.

---

## What it does

1. **Create plan** — Given `url` and `bug_description`, an LLM returns a structured `Plan` (ordered browser interactions only).
2. **Execute plan** — For the current step, a tool-using agent connects to Playwright MCP, opens the page with `browser_navigate`, follows the step, and returns `StepFindings`.
3. **Accumulate findings** — Step results append into `step_findings` via a LangGraph reducer so you can inspect every step’s outcome.

Primary graph: `insightaiagent` (`create_plan` → `execute_plan`).

Standalone graphs also exist for iterating on each phase in isolation:

| Graph ID         | Entry                                      | Purpose                          |
|------------------|--------------------------------------------|----------------------------------|
| `insightaiagent` | `src/insightaiagent/agent.py:graph`        | Full plan + execute pipeline     |
| `create_plan`    | `src/create_plan.py:graph`                 | Plan generation only             |
| `execute_plan`   | `src/execute_plan.py:graph`                | Execution loop (with conditional edge back to execute) |

---

## Architecture

```text
                    ┌─────────────────┐
   url + bug  ───►  │  create_plan    │ ── structured Plan
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  execute_plan   │◄──── Playwright MCP tools
                    │  (create_agent) │      (browser_navigate, …)
                    └────────┬────────┘
                             │
                             ▼
                      StepFindings[]
```

### Design choices

- **Plan vs execute** — Planning is structured output only (no tools). Execution is a LangChain `create_agent` loop with MCP tools and `response_format=StepFindings`.
- **No “open browser” tool** — Playwright MCP launches the browser on the first `browser_navigate`. Plans and prompts are written accordingly.
- **Graph-controlled steps, free rein inside a step** — The graph owns which step runs; the model may use several browser tools to complete that step.
- **OpenAI-compatible MCP schemas** — Some Playwright tool JSON Schemas use keywords OpenAI rejects (`propertyNames`, free-form maps, etc.). `PlaywrightMCP` sanitizes schemas and drops `browser_drop` before binding tools.

---

## Project layout

```text
InsightAIAgent/
├── langgraph.json              # Graph registry for langgraph dev / Studio
├── pyproject.toml              # Dependencies (uv / pip)
├── .env.example                # Required env vars (copy to .env)
├── src/
│   ├── create_plan.py          # Standalone create_plan graph
│   ├── execute_plan.py         # Standalone execute_plan graph (+ loop edge)
│   ├── insightaiagent/
│   │   └── agent.py            # Combined insightaiagent graph
│   ├── mcp_clients/
│   │   └── playwright.py       # MCP client + OpenAI schema sanitization
│   └── utils/
│       ├── model.py            # ChatOpenAI (gpt-4o)
│       ├── nodes.py            # create_plan / execute_plan node logic
│       ├── edges.py            # execute_plan ↔ END conditional routing
│       ├── states.py           # InsightGraphState / ExecutionState
│       ├── objects.py          # Plan, Steps, StepFindings (Pydantic)
│       └── prompts.py          # System prompts for plan + execute
└── README.md
```

---

## Prerequisites

- **Python** ≥ 3.11  
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip  
- **Node.js** ≥ 20 (for Playwright MCP via `npx`)  
- **OpenAI API key**  
- Optional: **LangSmith** API key for Studio tracing  

---

## Setup

### 1. Install Python deps

```powershell
cd InsightAIAgent
uv sync
```

Or with pip:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. Configure environment

```powershell
copy .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-...

# Optional — LangSmith / Studio tracing
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_...
LANGCHAIN_PROJECT=insightaiagent
```

`TAVILY_API_KEY` is listed in `.env.example` for future research tooling; the current browser QA path does not require it.

### 3. Start Playwright MCP (required for execute)

In a **separate** terminal, start a headed Playwright MCP HTTP server:

```powershell
npx @playwright/mcp@latest --port 8931
```

You should see it listening on `http://localhost:8931`. The client expects:

```text
http://localhost:8931/mcp
```

Keep this process running for the whole execute session. Closing it mid-run causes `mcp_unreachable`.

**Useful flags:**

| Flag            | Effect                                      |
|-----------------|---------------------------------------------|
| (default)       | Headed browser — you can watch the agent    |
| `--headless`    | No visible window                           |
| `--browser=chrome` | Prefer Chrome channel                    |
| `--port 8931`   | HTTP transport for LangGraph / IDE workers  |

---

## Running with LangGraph Studio

### Start the API

```powershell
langgraph dev
```

Typical output:

```text
- API:        http://127.0.0.1:2024
- Studio UI:  https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
- Docs:       http://127.0.0.1:2024/docs
```

### Studio “Failed to fetch”

Studio is HTTPS; your API is HTTP on localhost. Modern Chrome often blocks that unless you allow local network access:

1. Open Studio on `smith.langchain.com`
2. Site settings → **Local network access** → **Allow**
3. Reload

Alternatives: use Edge/Chrome (not Safari), disable Brave Shields, or run:

```powershell
langgraph dev --tunnel
```

and connect Studio to the printed HTTPS tunnel URL.

### Invoke input

Select graph **`insightaiagent`** (or a standalone graph) and submit state like:

```json
{
  "url": "https://example.com/app",
  "bug_description": "Submitting the login form with a valid password shows a blank page",
  "max_tools_turns": 30
}
```

| Field               | Required | Description                                      |
|---------------------|----------|--------------------------------------------------|
| `url`               | yes      | Page under test                                  |
| `bug_description`   | yes      | What the user sees going wrong                   |
| `expected_behavior` | no       | Optional expected behavior hint                  |
| `max_tools_turns`   | no       | Caps agent recursion (`default` 30 → limit ~60)  |
| `plan`              | execute only | Pre-built plan if skipping `create_plan`     |
| `current_step`      | execute only | 1-based step index (default from create_plan) |

---

## State model

### `InsightGraphState`

| Key              | Type                         | Notes |
|------------------|------------------------------|-------|
| `bug_description`| `str`                        | Input |
| `url`            | `str`                        | Input |
| `plan`           | `Plan`                       | Set by `create_plan` |
| `current_step`   | `int`                        | 1-based index into `plan.instructions` |
| `step_findings`  | `list[StepFindings]`         | **Append-only** via `operator.add` — always return `[finding]`, never a bare object |
| `completed`      | `bool`                       | True when all steps done (or flow stopped) |
| `error`          | `str \| null`                | Last step error / transport failure |
| `max_tools_turns`| `int`                        | Tool-loop budget for the execute agent |

### Domain objects (`utils/objects.py`)

- **`Plan`** — `goal`, `instructions: list[Steps]`, `steps`, `completed`
- **`Steps`** — `step`, `action`, `completed`, `findings`
- **`StepFindings`** — `findings`, `page_snapshot`, `error`, `console_messages`

---

## How execution talks to the browser

1. `PlaywrightMCP` opens an `MCPAdapter` session to `http://localhost:8931/mcp`.
2. Tools are listed, sanitized for OpenAI function calling, and passed to `create_agent`.
3. The agent is instructed to:
   - `browser_navigate` first (opens the browser)
   - `browser_snapshot` after meaningful actions (use element refs)
   - use console/network tools when they help confirm the bug
4. Structured `StepFindings` are written back onto the plan step and appended to `step_findings`.

**Important:** Prefer one long-lived MCP process. Opening/closing MCP per micro-action resets browser context; the current node opens a session for each `execute_plan` invocation.

Common Playwright tools used by the agent:

| Tool                         | Role                                      |
|------------------------------|-------------------------------------------|
| `browser_navigate`           | Open URL / start browser                  |
| `browser_snapshot`           | Accessibility tree + element refs         |
| `browser_click` / `browser_type` | Interact using snapshot refs           |
| `browser_wait_for`           | Wait for text / time                      |
| `browser_console_messages`   | Capture JS console output                 |
| `browser_network_requests`   | Inspect traffic for failed calls          |
| `browser_take_screenshot`    | Visual capture (actions still use snapshot) |

`browser_drop` is excluded — its schema is incompatible with OpenAI strict tool parameters.

---

## Local package naming note

Do **not** put application code in a package named `mcp` under `src/`. Running scripts from `src/` puts that directory first on `sys.path`, which shadows the official PyPI `mcp` SDK and breaks FastMCP / LangChain MCP imports (`No module named 'mcp.client'`). This project uses `mcp_clients/` instead.

---

## Development tips

- After changing graph/node code, let `langgraph dev` reload (watch mode). If Studio shows stale errors, start a **new thread** — old threads can retain incompatible state shapes (e.g. a single `StepFindings` instead of a list).
- Health-check the API: open `http://127.0.0.1:2024/ok` → `{"ok":true}`.
- Health-check MCP: ensure `npx @playwright/mcp@latest --port 8931` is still running when execute starts.
- Model defaults to `gpt-4o` in `src/utils/model.py`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Studio: Failed to fetch | Browser blocks HTTPS → localhost HTTP | Allow local network access, or `langgraph dev --tunnel` |
| `No module named 'mcp.client'` | Local package named `mcp` shadows SDK | Keep client code in `mcp_clients/`, not `src/mcp/` |
| `ConnectError` / `mcp_unreachable` | Playwright MCP not running | `npx @playwright/mcp@latest --port 8931` |
| OpenAI 400 `invalid_function_parameters` on a tool | Unsupported JSON Schema keywords | Handled in `playwright.py`; incompatible tools are dropped |
| `StepFindings + list` TypeError | Reducer expects a list | Always return `{"step_findings": [finding]}`; use a new Studio thread |
| `unhashable type: 'HumanMessage'` | Wrapped message in `{}` as a dict key | Pass `HumanMessage(...)` directly in the `messages` list |
| Graph load: got `module` | `add_node` passed an imported module | Import callables from `utils.nodes`, not `import create_plan` |

---

## Roadmap / known gaps

- The combined `insightaiagent` graph currently runs **one** `execute_plan` pass after planning. A step loop edge (`execute_plan_edge`) exists for the standalone `execute_plan` graph so remaining steps can re-enter until `completed`.
- MCP session is opened per execute node call; a shared session across steps would preserve cookies/tabs more reliably.
- No automated eval suite yet for plan quality or reproduction success rate.

---

## License / authors

See `pyproject.toml` for package metadata (`insightaiagent`, author `c-blanding`).
