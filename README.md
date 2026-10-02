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
| `auth_profile_id` | Optional name of a saved browser session. The password is not an input. |
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
│   │   ├── auth/                 # encrypted session store + capture CLI
│   │   ├── db/                   # SQLAlchemy models + Database helper
│   │   ├── evidence/             # shared Playwright session + media upload
│   │   ├── create_plan.py
│   │   ├── execute_plan.py
│   │   ├── insightaiagent/agent.py
│   │   ├── mcp_clients/playwright.py
│   │   └── utils/                # nodes, prompts, states, edges, model
│   └── test_web/                 # LumenShop demo storefront (optional QA target)
└── README.md
```

Playwright integration: `PlaywrightMCP` talks to `http://localhost:8931/mcp`, sanitizes tool schemas for OpenAI, and keeps cookie/storage/evaluate tools off the model tool list.

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

Start Playwright MCP in a separate terminal (headed browser by default). All
snapshots, video, and traces stay under `.playwright-mcp` (or `INSIGHT_MEDIA_DIR`).
Video also needs Playwright's ffmpeg binary once:

```powershell
npx playwright install ffmpeg
.\scripts\start-playwright-mcp.bat
```

Or manually:

```powershell
npx @playwright/mcp@latest --port 8931 --caps=storage,devtools --isolated --output-dir .playwright-mcp
```

`--caps=devtools` is required for video. Without it, `browser_start_video` is missing
and runs continue with no recording. For hosted / sidecar deploys set `PLAYWRIGHT_MCP_URL`
to the MCP HTTP URL and point both processes at the same absolute `INSIGHT_MEDIA_DIR`
(and Neon `DATABASE_URL` / `AWS_*` for durable evidence). Local files under
`.playwright-mcp` are removed when the graph finishes and uploads succeed.

Run the LangGraph dev server:

```powershell
langgraph dev
```

Use Studio or the local API to invoke **`insightaiagent`**. If Studio cannot reach localhost, allow local network access for LangSmith or run `langgraph dev --tunnel`.

## Auth sessions and security

Authenticated tests may use an optional `auth_profile_id`. That name is not a secret. The password, cookies, and storage-state JSON never go in graph input, checkpoints, or prompts.

**Login wall (Continue button)**

Auth is not requested up front. If a plan step hits a login page, the executor sets `error` to `auth_expired` without completing the step. The graph routes to `wait_for_login`, which calls LangGraph `interrupt` so Studio (or your frontend) can show **Continue**. Sign in in the headed Playwright window, then Continue with an empty resume or `{ "confirmed": true }` — never the password. After resume the node exports the browser storage state, encrypts it into Postgres, sets `auth_profile_id` if missing, and retries the same step.

**What must stay out of the graph**

- Do not put a password, cookie jar, `Authorization` header, or Playwright storage-state JSON in `bug_description`, `expected_behavior`, the Continue/resume payload, or any other state field. Those values are checkpointed and can be traced (including LangSmith if tracing is on).
- The model does not receive cookie tools, `localStorage` / `sessionStorage` tools, `browser_storage_state`, `browser_set_storage_state`, `browser_evaluate`, `browser_run_code_unsafe`, or video start/stop tools. Network and console tool output is redacted (cookies, auth headers, API keys, JWTs, and common password/token JSON fields) before it is stored on a step or sent to the report model.

**Where the session lives**

- Cookies and `localStorage` for one origin are encrypted with AES-GCM using `AUTH_DATA_KEY` (from `.env` on this machine). Only ciphertext, nonce, origin, and expiry are written to Postgres (`AUTH_DATABASE_URL`, or `DATABASE_URL` when unset).
- The ciphertext is bound to `profile_id` + `origin`, so swapping rows in the database fails decryption.
- A remote database URL must set `sslmode=require` (Neon), `verify-ca`, or `verify-full`. Prefer a role that can only `SELECT` / `INSERT` / `UPDATE` / `DELETE` on `auth_sessions`, not a superuser. Tables are managed via SQLAlchemy models in `insight/agent/src/db/` (see also `auth/schema.sql` / `evidence/schema.sql`).
- Scratch files under `.insight_auth/` (gitignored) exist only while Playwright restores or exports the session, then are deleted. On Windows the directory is locked to the current user with `icacls`.

**Optional CLI setup**

You can still pre-save a session without waiting for a login wall:

```powershell
.\.venv\Scripts\python.exe insight\agent\src\auth\capture.py generate-key
# put AUTH_DATA_KEY and AUTH_DATABASE_URL in .env (see .env.example)

.\.venv\Scripts\python.exe insight\agent\src\auth\capture.py save --profile my-app --origin https://app.example.com --login-url https://app.example.com/login
.\.venv\Scripts\python.exe insight\agent\src\auth\capture.py list
.\.venv\Scripts\python.exe insight\agent\src\auth\capture.py revoke --profile my-app
```

If `auth_profile_id` is set and a matching unexpired row exists, the shared Playwright client restores it once before the model runs. Changing `AUTH_DATA_KEY` does not re-encrypt old rows; revoke and save again.

**Limits**

Playwright storage state covers cookies and `localStorage` only. Sessions that live only in `sessionStorage` are not restored. LumenShop’s login redirects on success but does not set a cookie or `localStorage`, so a capture for that demo is rejected until the site under test actually stores a session. `interrupt` requires a checkpointer (provided by `langgraph dev` / Studio).

## Run evidence (screenshots, video, console, network, snapshots)

One Playwright MCP client is reused for the whole LangGraph thread. The harness (not the model) owns durable evidence:

| Kind | When | Where |
|------|------|--------|
| Screenshot | After navigate / click / type / fill / select / keypress | Neon bucket `insight-screenshots` + `run_artifacts` |
| Video | Continuous for the run (`browser_start_video` → stop on finish) | Neon bucket `insight-videos` + `run_artifacts` |
| Console | End of each step (errors + last lines, redacted, capped) | Postgres `run_logs` (`kind=console`) |
| Network | After navigate / form fill / submit-like clicks (failures and HTTP ≥ 400) | Postgres `run_logs` (`kind=network`) |
| Snapshot | After each `browser_snapshot` (a11y tree, redacted, capped) | Postgres `run_logs` (`kind=snapshot`) |

Screenshot and video bytes go to private Neon Object Storage. `run_artifacts` stores `thread_id`, `step`, `kind`, `bucket`, `object_key` only (see `insight/agent/src/evidence/schema.sql`). Console, network, and accessibility snapshots are logged via [`utils.logging.Logging`](insight/agent/src/utils/logging.py) into `run_logs` — not uploaded as objects and not listed on `StepFindings.artifacts`. `StepFindings.artifacts` carries screenshot/video keys into the report. Viewing media uses a short-lived presign (`evidence.store.presign_get`).

Login walls skip screenshots when the page looks like a password form, and `auth_expired` steps drop screenshot artifacts. Evidence upload/logging is best-effort: missing AWS env or tables logs a warning and the QA run continues.

Start MCP with `--output-dir .playwright-mcp` (gitignored). Screenshot pointers
(`bucket` + `object_key`) stay on `step_findings` for the whole run. Local PNGs/
video under that directory are removed when the graph finishes and uploads succeed.

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
