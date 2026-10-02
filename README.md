# Insight AI Agent

Insight AI Agent is a LangGraph application that takes a URL and a bug description, plans how to reproduce the issue in a real browser, runs that plan through Playwright MCP, and produces a structured QA report. Work stays in the browser: navigation, snapshots, clicks, typing, console, and network—no code-change recommendations in the execution path.

Evidence (screenshots, video, console/network/snapshot logs, timeline events) and a full run summary (plan, findings, timeline, report) are persisted to Neon Postgres and Object Storage via SQLAlchemy.

**Programmatic entry point:** [`InsightAgent`](src/insightai/agent.py) (`arun` / `acontinue`).  
**Studio entry point:** graph id `insightaiagent` via [`insightaiagent_graph.py`](src/insightai/insightaiagent_graph.py).  
**FastAPI integration notes:** [`docs/FASTAPI_HANDOFF.md`](docs/FASTAPI_HANDOFF.md).

## Pipeline

The main graph **`insightaiagent`** flow:

1. **Create plan** — LLM outputs a structured `Plan`: ordered steps that are browser interactions only (aligned with Playwright MCP tools).
2. **Execute plan** — A tool-using agent connects to Playwright MCP, opens the URL, works through plan steps, and returns `StepFindings` per step (with screenshots, console/network/snapshot logs, and timeline events). Loops until all steps complete.
3. **Finalize report** — LLM synthesizes `step_findings`, optional `expected_behavior`, timeline, and plan completion into a `Report`, then upserts a `runs` row.

Optional side path: if a step hits a login wall (`auth_expired`), **wait_for_login** interrupts for Continue, saves the session, then returns to **execute_plan**. Most runs never enter that node.

```text
START
  │
  ▼
create_plan
  │
  ▼
execute_plan ◄──────────────┐
  │                         │
  ├── more steps ───────────┘
  │
  ├── auth_expired only ──► wait_for_login ──► execute_plan
  │                         (interrupt → Continue)
  │
  └── done / error
        │
        ▼
  finalize_report ──► Report + runs upsert
        │
        ▼
       END
```

## Graphs

Registered in `langgraph.json`:

| Graph ID | Module | Use |
|----------|--------|-----|
| `insightaiagent` | `src/insightai/insightaiagent_graph.py` | End-to-end plan → execute → report |
| `create_plan` | `src/insightai/create_plan.py` | Plan generation only |
| `execute_plan` | `src/insightai/execute_plan.py` | Execution with conditional loop until complete |

## Programmatic API (`InsightAgent`)

Prefer this over calling the raw Studio `graph` from application code. The class validates input, assigns a `thread_id`, uses a checkpointer (default in-memory) so login interrupts can resume, and returns typed outcomes.

```python
from insightai import InsightAgent, NeedsLogin

agent = InsightAgent()

async def main():
    outcome = await agent.arun({
        "url": "http://localhost:5500/products.html",
        "bug_description": "Searching for mug returns no products",
        "expected_behavior": "Search matches product names case-insensitively",
        "max_tools_turns": 30,
    })
    if isinstance(outcome, NeedsLogin):
        # Sign in in the headed Playwright window, then:
        outcome = await agent.acontinue(outcome.thread_id)
    print(outcome.report)
```

| Method | Role |
|--------|------|
| `arun(state, *, thread_id=None, timeout=None)` | Run the full pipeline (async) |
| `acontinue(thread_id, *, cancel=False)` | Resume after a login wall |
| `astream(state, ...)` | Yield LangGraph progress chunks |
| `get_timeline(thread_id)` / `get_run(thread_id)` | Load persisted evidence / run row |
| `run` / `continue_after_login` | Sync wrappers — **do not** use inside FastAPI/uvicorn |

Outcomes:

- **`NeedsLogin`** — paused for human Continue (`thread_id`, `message`, `login_url`, `origin`, `auth_profile_id`)
- **`InsightResult`** — terminal state (`report`, `plan`, `step_findings`, `timeline`, `error`, `completed`, `ok`)

Never put passwords, cookies, or storage-state JSON in `arun` / `acontinue` payloads — validation rejects those keys.

> **Production note:** default checkpointer is `MemorySaver`. Multi-worker or durable login resume needs a shared LangGraph checkpointer (e.g. Postgres). See the FastAPI handoff.

## Inputs and state

Typical Studio or `InsightAgent.arun` input (`CreateGraphState`):

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
| `url` | Application under test (required, absolute `http(s)` URL) |
| `bug_description` | What is going wrong (required) |
| `expected_behavior` | Optional; used when writing the final report |
| `auth_profile_id` | Optional name of a saved browser session. The password is not an input. |
| `max_tools_turns` | Budget for tool use during a step (default 30, max 200) |

`thread_id` is **not** graph state — pass it to `arun(..., thread_id=...)` (or let the agent generate one). It keys evidence, Playwright session reuse, and DB rows.

Important runtime fields: `plan`, `current_step`, `step_findings` (append-only list), `completed`, `error`, `report`, `timeline`.

State typing lives in `src/insightai/utils/states.py`:

| TypedDict | Role |
|-----------|------|
| `CreateGraphState` | Caller input to `create_plan` / `arun` |
| `InsightGraphState` | Full LangGraph channel (superset) |
| `ExecutionState` | Execute / loop fields |
| `ReportState` | Inputs to `finalize_report` (report is output-only) |

Domain models live in `src/insightai/utils/objects.py`:

| Model | Role |
|-------|------|
| `Plan`, `Steps`, `StepFindings`, `ArtifactRef` | Plan + per-step findings / media refs |
| `Report` | Final QA write-up |
| `Timeline`, `TimelineEvent`, … | Merged timeline / action-log view |
| `Run` | Persisted run summary (inputs + plan + findings + timeline + report) |

## Repository layout

```text
InsightAIAgent/
├── langgraph.json
├── pyproject.toml
├── neon.ts                     # Neon config (Auth, Object Storage buckets)
├── .env.example
├── docs/
│   └── FASTAPI_HANDOFF.md      # Backend API integration guide
├── scripts/
│   └── start-playwright-mcp.bat
├── src/
│   ├── insightai/              # installable Python package
│   │   ├── agent.py            # InsightAgent (programmatic API)
│   │   ├── insightaiagent_graph.py
│   │   ├── create_plan.py
│   │   ├── execute_plan.py
│   │   ├── runtime_paths.py
│   │   ├── auth/               # encrypted session store + capture CLI
│   │   ├── db/                 # SQLAlchemy models, Database helper, schema SQL
│   │   ├── evidence/           # session, capture, uploads, timeline, save_run
│   │   ├── mcp_clients/playwright.py
│   │   └── utils/              # nodes, prompts, states, edges, model, logging
│   ├── api/                    # FastAPI service (WIP)
│   ├── web/                    # Frontend (WIP)
│   └── test_web/               # LumenShop demo storefront (optional QA target)
└── README.md
```

Import the package after `uv sync`:

```python
from insightai import InsightAgent, NeedsLogin
```

Playwright integration: `PlaywrightMCP` talks to `http://localhost:8931/mcp` (or `PLAYWRIGHT_MCP_URL`), sanitizes tool schemas for OpenAI, and keeps cookie/storage/evaluate tools off the model tool list.

## Requirements

- Python ≥ 3.11
- OpenAI API key
- Node.js (for Playwright MCP via `npx`)
- Neon Postgres (`DATABASE_URL`) for runs, logs, timeline, and auth sessions
- Optional: Neon Object Storage (`AWS_*` S3-compatible vars) for screenshot/video uploads
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

Set at least `OPENAI_API_KEY` and `DATABASE_URL` in `.env`. Prefer the pooled Neon URL for app traffic and `DATABASE_URL_UNPOOLED` for schema/DDL. See `.env.example` for auth, Object Storage, and Playwright MCP options.

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

## Database (SQLAlchemy + Neon)

Connection and CRUD live in `src/insightai/db/`:

```python
from insightai.db import get_database, Run
from insightai.utils.objects import Run as RunModel  # pydantic summary

db = get_database()       # DATABASE_URL (pooled OK)
db.create_tables()        # idempotent; prefer DATABASE_URL_UNPOOLED for first DDL

db.upsert_run(...)
db.insert_run_artifact(...)
db.insert_run_log(...)
db.insert_run_event(...)
```

| Table | Purpose | Schema |
|-------|---------|--------|
| `runs` | Full run summary: bug, URL, expected behavior, plan, step_findings, timeline, report | `db/schema_runs.sql` |
| `run_artifacts` | Screenshot/video object-storage metadata (`bucket` + `object_key`) | `evidence/schema.sql` |
| `run_logs` | Console / network / snapshot / info text logs | `db/schema.sql` |
| `run_events` | Ordered timeline / action breadcrumbs | `evidence/schema_events.sql` |
| `auth_sessions` | Encrypted Playwright storage-state blobs | `auth/schema.sql` |

Helpers:

- `Database.from_env()` / `get_database()` — app traffic (`DATABASE_URL`)
- `Database.for_migrations()` — DDL (`DATABASE_URL_UNPOOLED` when set)
- `Database.for_auth()` / `get_auth_database()` — `AUTH_DATABASE_URL` or `DATABASE_URL`
- Pooled Neon hosts (`-pooler`) use `NullPool`

Tables are also created on first use via `create_tables()` when the process can reach Postgres.

## Auth sessions and security

Authenticated tests may use an optional `auth_profile_id`. That name is not a secret. The password, cookies, and storage-state JSON never go in graph input, checkpoints, or prompts.

**Login wall (Continue button)**

Auth is not requested up front. If a plan step hits a login page, the executor sets `error` to `auth_expired` without completing the step. The graph routes to `wait_for_login`, which calls LangGraph `interrupt` so Studio (or your frontend / `InsightAgent.acontinue`) can show **Continue**. Sign in in the headed Playwright window, then Continue with an empty resume or `{ "confirmed": true }` — never the password. After resume the node exports the browser storage state, encrypts it into Postgres, sets `auth_profile_id` if missing, and retries the same step.

**What must stay out of the graph**

- Do not put a password, cookie jar, `Authorization` header, or Playwright storage-state JSON in `bug_description`, `expected_behavior`, the Continue/resume payload, or any other state field. Those values are checkpointed and can be traced (including LangSmith if tracing is on).
- The model does not receive cookie tools, `localStorage` / `sessionStorage` tools, `browser_storage_state`, `browser_set_storage_state`, `browser_evaluate`, `browser_run_code_unsafe`, or video start/stop tools. Network and console tool output is redacted (cookies, auth headers, API keys, JWTs, and common password/token JSON fields) before it is stored on a step or sent to the report model.

**Where the session lives**

- Cookies and `localStorage` for one origin are encrypted with AES-GCM using `AUTH_DATA_KEY` (from `.env` on this machine). Only ciphertext, nonce, origin, and expiry are written to Postgres (`AUTH_DATABASE_URL`, or `DATABASE_URL` when unset).
- The ciphertext is bound to `profile_id` + `origin`, so swapping rows in the database fails decryption.
- A remote database URL must set `sslmode=require` (Neon), `verify-ca`, or `verify-full`. Prefer a role that can only `SELECT` / `INSERT` / `UPDATE` / `DELETE` on `auth_sessions`, not a superuser.
- Scratch files under `.playwright-mcp/auth` or `INSIGHT_AUTH_SCRATCH_DIR` (gitignored) exist only while Playwright restores or exports the session, then are deleted. On Windows the directory is locked to the current user with `icacls`.

**Optional CLI setup**

You can still pre-save a session without waiting for a login wall:

```powershell
.\.venv\Scripts\python.exe -m insightai.auth.capture generate-key
# put AUTH_DATA_KEY and AUTH_DATABASE_URL (or DATABASE_URL) in .env

.\.venv\Scripts\python.exe -m insightai.auth.capture save --profile my-app --origin https://app.example.com --login-url https://app.example.com/login
.\.venv\Scripts\python.exe -m insightai.auth.capture list
.\.venv\Scripts\python.exe -m insightai.auth.capture revoke --profile my-app
```

If `auth_profile_id` is set and a matching unexpired row exists, the shared Playwright client restores it once before the model runs. Changing `AUTH_DATA_KEY` does not re-encrypt old rows; revoke and save again.

**Limits**

Playwright storage state covers cookies and `localStorage` only. Sessions that live only in `sessionStorage` are not restored. LumenShop’s login redirects on success but does not set a cookie or `localStorage`, so a capture for that demo is rejected until the site under test actually stores a session. `interrupt` requires a checkpointer (provided by `langgraph dev` / Studio, or by `InsightAgent`).

## Run evidence (screenshots, video, console, network, snapshots)

One Playwright MCP client is reused for the whole LangGraph thread. The harness (not the model) owns durable evidence:

| Kind | When | Where |
|------|------|--------|
| Screenshot | After navigate / click / type / fill / select / keypress | Neon bucket `insight-screenshots` + `run_artifacts` |
| Video | Continuous for the run (`browser_start_video` → stop on finish) | Neon bucket `insight-videos` + `run_artifacts` |
| Console | End of each step (errors + last lines, redacted, capped) | Postgres `run_logs` (`kind=console`) |
| Network | After navigate / form fill / submit-like clicks (failures and HTTP ≥ 400) | Postgres `run_logs` (`kind=network`) |
| Snapshot | After each `browser_snapshot` (a11y tree, redacted, capped) | Postgres `run_logs` (`kind=snapshot`) |

Screenshot and video bytes go to private Neon Object Storage. `run_artifacts` stores `thread_id`, `step`, `kind`, `bucket`, `object_key` only (`kind` is `screenshot` \| `video`). Console, network, and accessibility snapshots are logged via [`utils.logging.Logging`](src/insightai/utils/logging.py) into `run_logs` — not uploaded as objects and not listed on `StepFindings.artifacts`. `StepFindings.artifacts` carries screenshot/video keys into the report. Viewing media uses a short-lived presign (`evidence.store.presign_get`).

Login walls skip screenshots when the page looks like a password form, and `auth_expired` steps drop screenshot artifacts. Evidence upload/logging is best-effort: missing AWS env or tables logs a warning and the QA run continues.

Start MCP with `--output-dir .playwright-mcp` (gitignored). Screenshot pointers
(`bucket` + `object_key`) stay on `step_findings` for the whole run. Local PNGs/
video under that directory are removed when the graph finishes and uploads succeed.

### Timeline and action log

Every run writes structured breadcrumbs to Postgres `run_events` (session, steps,
tools, screenshots, snapshots, auth, uploads). Console/network/snapshot text stays
in `run_logs`; media pointers in `run_artifacts`.

`assemble_timeline(thread_id)` returns a `Timeline` model (events, actions, chapters, artifacts, logs). Dump a thread:

```powershell
.\.venv\Scripts\python.exe -m insightai.evidence.timeline_cli <thread_id> --pretty
.\.venv\Scripts\python.exe -m insightai.evidence.timeline_cli <thread_id> --actions-only --pretty
```

### Persisted runs

On `finalize_report`, `evidence.runs.save_run_from_state` upserts one `runs` row keyed by LangGraph `thread_id`, including:

- `bug_description`, `url`, `expected_behavior`
- `plan`, `step_findings`
- assembled `timeline`
- `report` (and `error` / `completed` when set)

Secrets in payloads are redacted before insert. Persistence is best-effort and must not fail report generation.

```python
from insightai.evidence import save_run_from_state, assemble_timeline
from insightai.db import get_database

# Manual save / load
save_run_from_state(state, config=config, report=report)
row = get_database().get_run(thread_id)
timeline = assemble_timeline(thread_id)
```

## Demo storefront

`src/test_web` is **LumenShop**, a static multi-page shop (catalog, cart, checkout, login, contact, account) meant for exercising the agent. Serve it while testing:

```powershell
python -m http.server 5500 --directory src/test_web
```

See `src/test_web/README.md` for run instructions and sample bug scenarios; `BUGS.md` lists planted defects for scoring runs.

## Development notes

- Default chat model: `gpt-4o` in `src/insightai/utils/model.py` (override with `OPENAI_MODEL`).
- Do not add a top-level Python package named `mcp` under the agent source tree; it shadows the official MCP SDK. This repo uses `mcp_clients/`.
- After graph or state shape changes, start a new Studio thread so checkpointed state does not conflict with reducers (e.g. `step_findings` must be appended as lists).
- Keep schema SQL, SQLAlchemy models, and the live Neon schema in sync when changing tables.
- FastAPI lives under `src/api/` (scaffold); follow [`docs/FASTAPI_HANDOFF.md`](docs/FASTAPI_HANDOFF.md) when wiring routes to `InsightAgent`.

## Package metadata

Python project name: `insightaiagent` (`pyproject.toml`). Runtime code for the agent lives under `src/insightai/`.
