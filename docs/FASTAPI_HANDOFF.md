# FastAPI Handoff — Insight AI Agent

**Audience:** Backend engineer building the FastAPI service that wraps this agent.  
**Primary integration surface:** `InsightAgent` in `insight/agent/src/insightaiagent/agent.py`  
**Do not** wire FastAPI to the raw LangGraph Studio module graph unless you intentionally want Studio parity without the class helpers (validation, typed outcomes, login resume).

---

## 1. What this product does

Insight AI Agent takes a **URL** and a **bug description**, plans browser reproduction steps with an LLM, executes them via **Playwright MCP** (real headed browser), and returns a structured **QA report** plus evidence (screenshots, video pointers, console/network/snapshot logs, timeline).

It does **not** suggest code changes. Work stays in the browser: navigate, snapshot, click, type, console, network.

### Pipeline (LangGraph)

```text
START → create_plan → execute_plan ⇄ (more steps | wait_for_login) → finalize_report → END
```

| Node | Role |
|------|------|
| `create_plan` | LLM → structured `Plan` (ordered browser steps) |
| `execute_plan` | Tool-using agent + Playwright MCP; appends `StepFindings`; loops until plan done or fatal error |
| `wait_for_login` | On `error == "auth_expired"`: **interrupts** for human Continue after sign-in in the browser |
| `finalize_report` | LLM → `Report`; best-effort upsert of `runs` row in Neon |

Routing after `execute_plan` (`insight_after_execute`):

- `auth_expired` → `wait_for_login`
- more steps → `execute_plan` again
- done / non-auth error → `finalize_report`

---

## 2. How FastAPI should talk to the agent

### Import / path

Today the package is **not** fully installed as a proper `src/` layout. Sibling modules (`utils`, `db`, `evidence`, `auth`, `mcp_clients`) live under `insight/agent/src/` and are importable either because:

1. `agent.py` inserts that directory onto `sys.path`, or  
2. Your FastAPI process adds `insight/agent/src` to `PYTHONPATH` / `sys.path` at startup.

Recommended app bootstrap:

```python
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[N] / "insight" / "agent" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from insightaiagent import (
    InsightAgent,
    InsightResult,
    NeedsLogin,
    InsightInputError,
    InsightPreflightError,
    InsightAgentError,
)
```

(If packaging is cleaned up later to `pip install -e .` with a real `insightaiagent` package root, drop the path hack and import normally.)

### Singleton agent

Create **one** `InsightAgent` per process (or per worker). It owns:

- A compiled LangGraph with a **checkpointer** (default `MemorySaver`)
- Playwright session lifecycle keyed by `thread_id`

```python
# lifespan
agent = InsightAgent()  # preflight checks OPENAI_API_KEY + DATABASE_URL
# Optional: InsightAgent(checkpointer=your_postgres_saver) for multi-worker durability
```

**Critical for production:** default checkpointer is **in-memory**. Login resume and mid-run state die if the worker restarts or if you load-balance across workers without sticky sessions / shared checkpointer. For multi-replica FastAPI, inject a Postgres (or Redis) LangGraph checkpointer and pass the **same** `thread_id` on continue.

### Prefer async APIs

| Use in FastAPI | Avoid in async routes |
|----------------|------------------------|
| `await agent.arun(...)` | `agent.run(...)` (`asyncio.run` — breaks under uvicorn) |
| `await agent.acontinue(...)` | `agent.continue_run(...)` |
| `async for ... in agent.astream(...)` | — |

---

## 3. Starting a run — input contract

Typed shape for create: `CreateGraphState` in `utils/states.py` (plus optional `thread_id` on the config, not in graph state).  
`InsightAgent.validate_input` / `arun` accept a plain `dict` with the same fields.

### Request body fields (map 1:1 to agent input)

| Field | Required | Type | Notes |
|-------|----------|------|--------|
| `url` | **yes** | string | Absolute `http://` or `https://` URL under test |
| `bug_description` | **yes** | string | What is going wrong |
| `expected_behavior` | no | string | Used when writing the final report |
| `auth_profile_id` | no | string | Name of a **saved** encrypted browser session (not a secret). Requires `AUTH_DATA_KEY` in env |
| `max_tools_turns` | no | int | Default **30**, clamped to **1…200** |
| `thread_id` | no | string | If omitted, agent generates a UUID (normalized). Pass your own for client correlation |

Agent method:

```python
outcome = await agent.arun(
    {
        "url": body.url,
        "bug_description": body.bug_description,
        "expected_behavior": body.expected_behavior,  # optional
        "auth_profile_id": body.auth_profile_id,      # optional
        "max_tools_turns": body.max_tools_turns,      # optional
    },
    thread_id=body.thread_id,   # optional
    timeout=600.0,              # optional wall-clock seconds
    check_mcp=True,             # probes Playwright MCP before run
)
```

### Forbidden input (validation rejects → `InsightInputError`)

Do **not** accept or forward any of these keys on create or continue payloads:

`password`, `passwd`, `secret`, `cookies`, `cookie`, `authorization`, `storage_state`, `storageState`, `access_token`, `refresh_token`, `api_key`, `apikey`  
(and anything matching a similar regex)

**Why:** Graph state is checkpointed (and may be traced in LangSmith). Passwords belong only in the headed Playwright window. Cookies/storage-state are encrypted into Postgres via the auth subsystem after Continue.

Suggested FastAPI status: **400** for `InsightInputError`.

---

## 4. Outcomes — two success shapes

`arun` / `acontinue` return a discriminated union:

```python
InsightOutcome = InsightResult | NeedsLogin
```

Branch with `isinstance`:

```python
if isinstance(outcome, NeedsLogin):
    # HTTP 202 + login UI payload
    ...
else:
    # InsightResult — completed, failed, or cancelled run
    ...
```

### 4.1 `NeedsLogin` (human-in-the-loop pause)

Frozen dataclass. Graph is **paused** at `wait_for_login`. Browser session for this `thread_id` is still held open.

| Field | Meaning |
|-------|---------|
| `thread_id` | **Must** be sent back on Continue |
| `message` | User-facing instructions |
| `login_url` | URL to sign into (same as run URL typically) |
| `origin` | Normalized origin for session save |
| `auth_profile_id` | Profile that will be written after Continue |
| `interrupt_id` | LangGraph interrupt id (optional to expose) |
| `raw` | Full interrupt value dict |

**Frontend contract:** Show “Sign in in the browser window, then click Continue.” User types the password **only** in Playwright, never in your API.

Suggested HTTP: **202 Accepted** with body like:

```json
{
  "status": "needs_login",
  "thread_id": "...",
  "message": "...",
  "login_url": "...",
  "origin": "...",
  "auth_profile_id": "..."
}
```

### 4.2 Continue after login

```python
outcome = await agent.acontinue(
    thread_id,
    resume=None,      # default → {"confirmed": true}
    cancel=False,     # True → abort sign-in → run ends with auth_cancelled
    timeout=600.0,
)
```

- Empty / `{ "confirmed": true }` = Continue  
- `{ "cancelled": true }` or `cancel=True` = abort  
- **Never** accept password/cookies in resume body (agent rejects forbidden keys)

May return another `NeedsLogin` (unusual) or `InsightResult`.

Suggested routes:

- `POST /runs` → start (`arun`)
- `POST /runs/{thread_id}/continue` → `acontinue`
- `POST /runs/{thread_id}/cancel-login` → `acontinue(..., cancel=True)`

### 4.3 `InsightResult` (terminal or stopped)

| Field | Type | Meaning |
|-------|------|---------|
| `thread_id` | str | Run id for evidence / DB |
| `url` | str | Input URL |
| `bug_description` | str | Input bug |
| `expected_behavior` | str \| None | Input |
| `auth_profile_id` | str \| None | May be set after login capture |
| `plan` | `Plan` \| None | Structured reproduction plan |
| `step_findings` | `list[StepFindings]` | Append-only per-step results |
| `report` | `Report` \| None | Final QA write-up (`report.report` text) |
| `timeline` | `Timeline` \| None | Assembled from DB when possible |
| `error` | str \| None | Stop/error code or message (see §6) |
| `completed` | bool | Plan finished and/or report present |
| `state` | dict | Raw final graph state (debug; strip before public API if needed) |
| `ok` | property | `report is not None and not error` |

Serialize Pydantic models with `.model_dump()` / `.model_dump(mode="json")`.

Suggested HTTP:

- `ok` → **200**
- finished with `error` / no report → **200** with `status: "failed"` **or** **422/500** depending on product preference (agent often still runs `finalize_report` on non-auth errors — check `report` + `error`)

---

## 5. Domain models (JSON shapes)

Defined in `insight/agent/src/utils/objects.py` (Pydantic).

### `Plan`

```json
{
  "goal": "...",
  "steps": 3,
  "completed": true,
  "instructions": [
    {
      "step": 1,
      "action": "Open the products page",
      "completed": true,
      "findings": { "...StepFindings..." }
    }
  ]
}
```

### `StepFindings`

```json
{
  "findings": "text",
  "page_snapshot": "a11y tree excerpt",
  "error": null,
  "network_messages": ["..."],
  "console_messages": ["..."],
  "artifacts": [
    { "kind": "screenshot", "bucket": "insight-screenshots", "object_key": "..." },
    { "kind": "video", "bucket": "insight-videos", "object_key": "..." }
  ]
}
```

Artifacts are **pointers only** (no bytes). To let clients view media, generate short-lived URLs via `evidence.store.presign_get(bucket, object_key, ...)`.

### `Report`

```json
{ "report": "markdown or prose QA write-up" }
```

### `Timeline`

Merged action log for the thread:

```json
{
  "thread_id": "...",
  "events": [ { "id", "t", "step", "event_type", "tool", "title", "detail", "status", "ref", "source" } ],
  "actions": [ "...subset..." ],
  "artifacts": [ { "id", "t", "step", "kind", "bucket", "object_key", "content_type", "byte_size" } ],
  "logs": [ { "id", "t", "step", "kind", "source", "message" } ],
  "chapters": [ { "step", "label", "events": [] } ],
  "error": null
}
```

Load anytime with:

```python
timeline = agent.get_timeline(thread_id)          # Timeline model
row = agent.get_run(thread_id)                    # SQLAlchemy Run row or None
# Optional: from utils.objects import Run; Run.from_row(row)
```

For an in-progress UI, poll `get_timeline` (or filter `run_events` by `after` id) alongside job status — see **Progress UX** under §7. Do not wait for `finalize_report`; events appear while the run is still executing.

---

## 6. Error / status codes from the agent

### Exceptions (raised before or around invoke)

| Exception | Typical cause | Suggested HTTP |
|-----------|---------------|----------------|
| `InsightInputError` | Bad/missing URL, forbidden keys, bad `max_tools_turns`, auth profile without `AUTH_DATA_KEY` | 400 |
| `InsightPreflightError` | Missing `OPENAI_API_KEY` / `DATABASE_URL`, or Playwright MCP unreachable | 503 |
| `InsightAgentError` | e.g. run `timeout` | 504 or 500 |
| Other exceptions | MCP crash, LLM failure, unexpected | 500; agent attempts Playwright `release(thread_id)` on failure |

### Soft errors on `InsightResult.error` (string codes / messages)

Common codes produced in nodes:

| Value | Meaning |
|-------|---------|
| `auth_expired` | Transient during run; should surface as `NeedsLogin`, not final result |
| `auth_cancelled` | User cancelled Continue |
| `mcp_unreachable` | Lost Playwright MCP mid-run |
| other tool / store codes | From `PlaywrightToolError` / `AuthStoreError` / storage-state errors |

Treat non-empty `error` without a usable `report` as a failed run for the UI.

---

## 7. Recommended FastAPI surface

Minimal viable API:

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | Env present + optional MCP probe (`agent.preflight(check_mcp=True)`) |
| `POST` | `/runs` | Body = `CreateGraphState` fields + optional `thread_id` → start job / `arun` |
| `POST` | `/runs/{thread_id}/continue` | `acontinue` |
| `GET` | `/runs/{thread_id}` | Job status + latest summary (`get_run` / in-memory outcome) |
| `GET` | `/runs/{thread_id}/timeline` | `agent.get_timeline` — progress breadcrumbs for the UI |
| `GET` | `/artifacts/presign` | Query `bucket` + `object_key` → signed GET URL |

### Long-running runs

A full QA run can take **minutes** (LLM + many browser steps). Options:

1. **Background task + polling** (recommended for v1)  
   - `POST /runs` returns `{ thread_id, status: "running" }` immediately  
   - Worker calls `arun`; stores outcome in Redis/DB  
   - Client polls `GET /runs/{thread_id}` for status (`running` / `needs_login` / `completed` / `failed`)  
   - If outcome is `NeedsLogin`, status becomes `needs_login`

2. **Synchronous wait** (dev only)  
   - `await arun` in the request with a high timeout — blocks the worker

**Recommendation:** job queue or `BackgroundTasks` + poll, with explicit `needs_login` state requiring `continue`.

### Progress UX — poll `run_events` / timeline (prefer over SSE)

Live “what’s going on” should come from **persisted timeline events**, not from LangGraph `astream`.

During a run the harness already writes breadcrumbs to Postgres `run_events` (step start/end, tools, screenshots, auth, session, etc.). `agent.get_timeline(thread_id)` merges those with artifacts/logs.

**v1: poll (recommended)**

- Poll every **1–2s** while the run tab is open.
- Prefer one response that returns **status + new events** so the client does not need two timers, e.g.:

```http
GET /runs/{thread_id}?after=<last_event_id>
```

```json
{
  "thread_id": "...",
  "status": "running",
  "events": [ { "id", "t", "step", "event_type", "tool", "title", "detail", "status" } ],
  "needs_login": null
}
```

- Cursor: `after` = last event `id` the client already has; return only newer rows (DB: `list_run_events` today returns the full list — filter in the API or add `id > after` later).
- Stop polling on terminal status or pause on `needs_login` until after `POST .../continue`.
- Events are sparse (seconds apart), so polling cost is low and reconnect / multi-tab / worker restart stay simple.

**Why not SSE for `run_events` in v1**

- Same data is already queryable; push adds heartbeats, reconnect, and “catch up from last id” without much UX gain for minute-long runs.
- Login interrupts and job status already force a request/response model; poll fits that naturally.
- Add **SSE later** only if the UI feels laggy — reuse the same `after` query as the poll endpoint so both transports share one cursor.

**Do not confuse with LangGraph streaming**

| Mechanism | What it shows | When to use |
|-----------|---------------|-------------|
| Poll timeline / `run_events` | Browser/step breadcrumbs users care about | **Default progress UI** |
| `agent.astream` → SSE/WS | LangGraph **node** updates (`create_plan`, `execute_plan`, …) | Optional; Studio-like graph debug |
| Inner tool/token stream | Clicks, LLM tokens inside `execute_plan` | Not exposed today (`ainvoke` inside the node) |

Caveats if you do add `astream` later: chunks are coarse (per plan step / node), and `astream` does **not** wrap interrupts as `NeedsLogin` — still finish via job status / `get_state` after the stream pauses.

### Concurrency notes

- One Playwright MCP client is held **per `thread_id`** for the life of the run (including login pause).
- Do not reuse the same `thread_id` for two concurrent runs.
- On timeout/exception the agent calls `release(thread_id)` to stop video and close the client.
- After successful completion, local media under `.playwright-mcp` is cleaned when uploads succeed; DB + object storage remain source of truth.
- `step_findings` uses an append reducer — always treat findings as a **list** to merge, never replace with a scalar in custom code.
---

## 8. Runtime dependencies (ops)

FastAPI alone is not enough. Each environment needs:

| Dependency | Why |
|------------|-----|
| **Playwright MCP** process | Browser automation (`PLAYWRIGHT_MCP_URL`, default `http://localhost:8931/mcp`) |
| Shared **media dir** | `INSIGHT_MEDIA_DIR` must match MCP `--output-dir` (default `.playwright-mcp`) |
| **OpenAI** | `OPENAI_API_KEY` (model via `OPENAI_MODEL`, default gpt-4o) |
| **Neon Postgres** | `DATABASE_URL` (pooled OK for app); runs, logs, events, artifacts metadata |
| **AUTH_DATA_KEY** | Required if using saved sessions / login capture |
| **Neon Object Storage** (optional) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION` — screenshots/video; missing → run continues without uploads |

Start MCP (example):

```powershell
npx playwright install ffmpeg
.\scripts\start-playwright-mcp.bat
# or:
# npx @playwright/mcp@latest --port 8931 --caps=storage,devtools --isolated --output-dir .playwright-mcp
```

`--caps=devtools` is required for video. `--caps=storage` is required for auth session export/restore.

See `.env.example` for the full variable list.

---

## 9. Persistence the API can lean on

| Store | Key | Contents |
|-------|-----|----------|
| `runs` | `thread_id` | bug, url, expected_behavior, plan, step_findings, timeline, report, error, completed |
| `run_artifacts` | thread_id + step | screenshot/video `bucket` + `object_key` |
| `run_logs` | thread_id | console / network / snapshot text |
| `run_events` | thread_id | timeline breadcrumbs — **poll these for live progress** (see §7) |
| `auth_sessions` | profile_id + origin | encrypted storage-state (not exposed to clients) |

`finalize_report` already upserts `runs` via `evidence.runs.save_run_from_state` (best-effort; DB failure is logged and does not fail the report). You usually **do not** need to write the report yourself — read it back with `get_run` / return `InsightResult`.

SQLAlchemy entry: `from db import get_database`.

---

## 10. Security requirements for the API

1. **Never** put passwords, raw cookies, or Playwright storage-state JSON in request bodies, logs, or webhook payloads.
2. Reject those fields at the FastAPI schema layer (defense in depth; agent also rejects).
3. `auth_profile_id` is a **label**, not a credential — still don’t let clients enumerate/delete others’ profiles without authz.
4. Artifact download: only expose **presigned** URLs with short TTL; buckets are private.
5. If LangSmith tracing is on, assume prompts/state may leave your VPC — keep secrets out of state.
6. Multi-tenant: scope `thread_id` to the authenticated user; don’t allow arbitrary thread access.

---

## 11. Example response envelopes (suggested)

### Create run → needs login

```http
HTTP/1.1 202 Accepted
```

```json
{
  "status": "needs_login",
  "thread_id": "a1b2c3d4-...",
  "message": "A login page appeared. Sign in in the headed Playwright browser...",
  "login_url": "https://app.example.com/login",
  "origin": "https://app.example.com",
  "auth_profile_id": "auto-app.example.com"
}
```

### Create / continue → success

```http
HTTP/1.1 200 OK
```

```json
{
  "status": "completed",
  "ok": true,
  "thread_id": "a1b2c3d4-...",
  "url": "https://app.example.com/products",
  "bug_description": "...",
  "expected_behavior": "...",
  "plan": { },
  "step_findings": [ ],
  "report": { "report": "..." },
  "timeline": { },
  "error": null,
  "completed": true,
  "auth_profile_id": "auto-app.example.com"
}
```

### Validation failure

```http
HTTP/1.1 400 Bad Request
```

```json
{ "detail": "url must be an absolute http(s) URL, got 'not-a-url'" }
```

### MCP down

```http
HTTP/1.1 503 Service Unavailable
```

```json
{ "detail": "Playwright MCP unreachable at http://localhost:8931/mcp. ..." }
```

---

## 12. Minimal integration sketch

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from insightaiagent import (
    InsightAgent,
    InsightInputError,
    InsightPreflightError,
    InsightAgentError,
    NeedsLogin,
)


class StartRunRequest(BaseModel):
    url: str
    bug_description: str
    expected_behavior: str | None = None
    auth_profile_id: str | None = None
    max_tools_turns: int | None = Field(default=None, ge=1, le=200)
    thread_id: str | None = None


class ContinueRequest(BaseModel):
    cancel: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = InsightAgent()
    yield


app = FastAPI(lifespan=lifespan)


def _serialize_outcome(outcome):
    if isinstance(outcome, NeedsLogin):
        return {
            "status": "needs_login",
            "thread_id": outcome.thread_id,
            "message": outcome.message,
            "login_url": outcome.login_url,
            "origin": outcome.origin,
            "auth_profile_id": outcome.auth_profile_id,
        }
    return {
        "status": "completed" if outcome.ok else "failed",
        "ok": outcome.ok,
        "thread_id": outcome.thread_id,
        "url": outcome.url,
        "bug_description": outcome.bug_description,
        "expected_behavior": outcome.expected_behavior,
        "auth_profile_id": outcome.auth_profile_id,
        "plan": outcome.plan.model_dump() if outcome.plan else None,
        "step_findings": [f.model_dump() for f in outcome.step_findings],
        "report": outcome.report.model_dump() if outcome.report else None,
        "timeline": outcome.timeline.model_dump() if outcome.timeline else None,
        "error": outcome.error,
        "completed": outcome.completed,
    }


@app.post("/runs")
async def start_run(body: StartRunRequest):
    agent: InsightAgent = app.state.agent
    payload = body.model_dump(exclude_none=True)
    thread_id = payload.pop("thread_id", None)
    try:
        outcome = await agent.arun(payload, thread_id=thread_id, timeout=900)
    except InsightInputError as e:
        raise HTTPException(400, str(e)) from e
    except InsightPreflightError as e:
        raise HTTPException(503, str(e)) from e
    except InsightAgentError as e:
        raise HTTPException(500, str(e)) from e
    data = _serialize_outcome(outcome)
    if data["status"] == "needs_login":
        return JSONResponse(data, status_code=202)
    return data


@app.post("/runs/{thread_id}/continue")
async def continue_run(thread_id: str, body: ContinueRequest):
    agent: InsightAgent = app.state.agent
    try:
        outcome = await agent.acontinue(
            thread_id, cancel=body.cancel, timeout=900
        )
    except InsightInputError as e:
        raise HTTPException(400, str(e)) from e
    except InsightAgentError as e:
        raise HTTPException(500, str(e)) from e
    data = _serialize_outcome(outcome)
    if data["status"] == "needs_login":
        return JSONResponse(data, status_code=202)
    return data
```

(Add `JSONResponse` import; promote to background jobs before production load.)

---

## 13. Key source files

| Path | Why it matters |
|------|----------------|
| `insight/agent/src/insightaiagent/agent.py` | **`InsightAgent`, outcomes, validation, graph factory** |
| `insight/agent/src/insightaiagent_graph.py` | LangGraph Studio / `langgraph.json` entry (`graph`) |
| `insight/agent/src/insightaiagent/__init__.py` | Public exports |
| `insight/agent/src/utils/states.py` | `CreateGraphState`, `InsightGraphState`, `ExecutionState`, `ReportState` |
| `insight/agent/src/utils/objects.py` | Pydantic domain models (`Plan`, `Report`, `Timeline`, `Run`, …) |
| `insight/agent/src/utils/nodes.py` | Node implementations + interrupt payload |
| `insight/agent/src/utils/prompts.py` | Plan / execute / report prompts |
| `insight/agent/src/utils/edges.py` | `auth_expired` routing |
| `insight/agent/src/evidence/session.py` | Per-thread Playwright client; `thread_id` normalization |
| `insight/agent/src/evidence/timeline.py` | `assemble_timeline`, `emit_event` |
| `insight/agent/src/evidence/runs.py` | `save_run` / `save_run_from_state` |
| `insight/agent/src/evidence/store.py` | Upload + `presign_get` |
| `insight/agent/src/db/` | SQLAlchemy models + `get_database()` |
| `insight/agent/src/auth/` | Encrypted session store (not for direct API exposure) |
| `.env.example` | Environment contract |
| `README.md` | Product behavior, MCP flags, auth threat model |
| `langgraph.json` | Studio-only; FastAPI should use `InsightAgent`, not Studio |

---

## 14. Open decisions for the backend eng

1. **Sync vs job API** — block on `arun` vs async job + poll (strongly prefer jobs).
2. **Checkpointer** — `MemorySaver` OK for single-worker demo; Postgres checkpointer required for multi-replica + durable login pause.
3. **Sticky Playwright** — MCP and API must share network access to the same headed browser host and the same `INSIGHT_MEDIA_DIR`.
4. **AuthN/Z** — who can start runs and read which `thread_id`s.
5. **Whether to expose** raw `InsightResult.state` (probably internal only).
6. **Progress transport** — **poll** status + `run_events` / timeline for v1 (1–2s, optional `after` cursor). Defer SSE on events and LangGraph `astream` until the job model is solid; `astream` does not wrap interrupts as `NeedsLogin`.
7. **Failed-but-reported runs** — when `error` is set but `finalize_report` still produced a `report`, decide if the API status is `completed`, `failed`, or `completed_with_errors`.

---

## 15. One-sentence integration rule

**Accept only `CreateGraphState` fields (no secrets) → start a background job with `await InsightAgent.arun` → poll status + timeline/`run_events` for progress → if `NeedsLogin`, return that state and later `acontinue(thread_id)` after the human signs in in the browser → return serialized `InsightResult` (and use `get_timeline` / `presign_get` for evidence).**
