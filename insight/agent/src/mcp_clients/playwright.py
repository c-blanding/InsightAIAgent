import copy
from typing import Any

from langchain.mcp import MCPAdapter
from langchain_core.tools import BaseTool

from auth.redact import redact_secrets
from runtime_paths import media_dir, playwright_mcp_url


def _default_mcp_url() -> str:
    return playwright_mcp_url()


# Start the server with storage + video tools, but do not give those tools to the model:
#   npx @playwright/mcp@latest --port 8931 --caps=storage,devtools --isolated --output-dir <INSIGHT_MEDIA_DIR>
_HIDDEN_FROM_MODEL = frozenset(
    {
        "browser_drop",
        "browser_evaluate",
        "browser_run_code_unsafe",
        "browser_storage_state",
        "browser_set_storage_state",
        "browser_start_video",
        "browser_stop_video",
        "browser_video_chapter",
        "browser_video_show_actions",
        "browser_video_hide_actions",
        "browser_take_screenshot",
    }
)
_REDACTED_TOOLS = frozenset(
    {
        "browser_console_messages",
        "browser_network_request",
        "browser_network_requests",
    }
)

# Schemas OpenAI cannot accept even after sanitization (free-form maps, etc.).

# OpenAI function parameters only accept a subset of JSON Schema.
_OPENAI_SCHEMA_BLOCKLIST = frozenset(
    {
        "$schema",
        "propertyNames",
        "patternProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependentRequired",
        "prefixItems",
        "contains",
        "minContains",
        "maxContains",
    }
)


def _sanitize_schema(node: Any) -> Any:
    """Make a JSON Schema acceptable to OpenAI function calling / strict tools."""
    if isinstance(node, list):
        return [_sanitize_schema(item) for item in node]

    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in _OPENAI_SCHEMA_BLOCKLIST:
            continue
        # OpenAI only allows boolean additionalProperties, not a nested schema.
        if key == "additionalProperties" and isinstance(value, dict):
            cleaned[key] = True
            continue
        cleaned[key] = _sanitize_schema(value)

    # Strict mode: every property key must appear in required.
    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["required"] = list(properties.keys())
        cleaned.setdefault("additionalProperties", False)

    return cleaned


def _hidden_from_model(name: str) -> bool:
    return (
        name in _HIDDEN_FROM_MODEL
        or name.startswith("browser_cookie_")
        or name.startswith("browser_localstorage_")
        or name.startswith("browser_sessionstorage_")
    )


def _redact_network_tool(tool: BaseTool) -> BaseTool:
    """Keep Cookie and Authorization values out of model-visible network logs."""
    if tool.name not in _REDACTED_TOOLS:
        return tool
    updates = {}
    original_async = tool.coroutine
    original_sync = tool.func
    if original_async is not None:

        async def _run(*args, _orig=original_async, **kwargs):
            return redact_secrets(await _orig(*args, **kwargs))

        updates["coroutine"] = _run
    if original_sync is not None:

        def _run_sync(*args, _orig=original_sync, **kwargs):
            return redact_secrets(_orig(*args, **kwargs))

        updates["func"] = _run_sync
    if not updates:
        return tool
    return tool.model_copy(update=updates)


_EVIDENCE_WRAP = frozenset(
    {
        "browser_navigate",
        "browser_navigate_back",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_press_key",
    }
)


def _wrap_evidence_tool(tool: BaseTool, client: "PlaywrightMCP") -> BaseTool:
    """After important UI actions, harness screenshots (and sometimes network)."""
    if tool.name not in _EVIDENCE_WRAP:
        return tool
    original_async = tool.coroutine
    if original_async is None:
        return tool

    async def _run(*args, _orig=original_async, _name=tool.name, _client=client, **kwargs):
        result = await _orig(*args, **kwargs)
        try:
            from evidence.capture import after_tool

            await after_tool(_client, _name, result)
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "Evidence harness skipped after %s", _name, exc_info=True
            )
        return result

    return tool.model_copy(update={"coroutine": _run})


def _openai_compatible_tools(
    tools: list[BaseTool], client: "PlaywrightMCP | None" = None
) -> list[BaseTool]:
    compatible: list[BaseTool] = []
    for tool in tools:
        if _hidden_from_model(tool.name):
            continue
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            tool.args_schema = _sanitize_schema(copy.deepcopy(schema))
        tool = _redact_network_tool(tool)
        if client is not None:
            tool = _wrap_evidence_tool(tool, client)
        compatible.append(tool)
    return compatible


class PlaywrightToolError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.code = "auth_restore_failed"


class PlaywrightMCP:
    """Client for a standalone Playwright MCP HTTP server.

    Start the server in headed mode (visible browser) in another terminal:

        npx @playwright/mcp@latest --port 8931 --caps=storage,devtools --isolated --output-dir <media_dir>

    Set ``PLAYWRIGHT_MCP_URL`` when the MCP server is not on localhost (sidecar /
    hosted). ``INSIGHT_MEDIA_DIR`` must match MCP ``--output-dir`` on a shared
    filesystem so screenshots and video paths resolve for both processes.

    ``--caps=storage`` enables save/restore of cookies. ``devtools`` enables
    video recording. Storage and video tools are called by this process only;
    they are removed from the tool list given to the model.
    ``--isolated`` keeps the durable copy in Postgres instead of a browser profile.
    """

    def __init__(self, url: str | None = None):
        self.url = (url or _default_mcp_url()).strip()
        self.adapter = MCPAdapter(self.url)
        self.tools = None
        self._tool_names: set[str] = set()
        self._entered = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

    def start_hint(self) -> str:
        return (
            f"npx @playwright/mcp@latest --port 8931 "
            f"--caps=storage,devtools --isolated --output-dir {media_dir()}"
        )

    def has_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def start(self):
        try:
            await self.adapter.__aenter__()
            self._entered = True
        except OSError as exc:
            raise ConnectionError(
                f"Playwright MCP is not reachable at {self.url}. "
                f"Start it with: {self.start_hint()}"
            ) from exc

        raw_tools = await self.adapter.list_tools()
        self._tool_names = {t.name for t in raw_tools}
        self.tools = _openai_compatible_tools(raw_tools, client=self)
        missing = [
            name
            for name in ("browser_start_video", "browser_set_storage_state")
            if name not in self._tool_names
        ]
        if missing:
            import logging

            logging.getLogger(__name__).warning(
                "Playwright MCP missing %s. Restart with: %s",
                ", ".join(missing),
                self.start_hint(),
            )
        return self.tools

    async def call_mcp(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke an MCP tool from this process. Result is not shown to the model."""
        if not self._entered:
            raise PlaywrightToolError("Playwright MCP session is not open")
        if self._tool_names and name not in self._tool_names:
            raise PlaywrightToolError(
                f"Playwright tool {name} is not available on {self.url}. "
                f"Start MCP with: {self.start_hint()}"
            )
        try:
            result = await self.adapter.client.call_tool(name, arguments or {})
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            # Keep message ASCII-safe for Windows consoles; drop box-drawing noise.
            detail = "".join(ch if ord(ch) < 128 else " " for ch in detail)
            detail = " ".join(detail.split())
            if len(detail) > 400:
                detail = detail[:400] + "..."
            hint = ""
            if "storage_state" in name or "cookie" in name or "video" in name:
                hint = f" Start Playwright MCP with: {self.start_hint()}"
            if "ffmpeg" in detail.lower():
                hint += " Also run: npx playwright install ffmpeg"
            raise PlaywrightToolError(
                f"Playwright tool {name} failed: {detail}.{hint}"
            ) from None
        if getattr(result, "is_error", False):
            raise PlaywrightToolError(f"Playwright tool {name} failed")
        return result

    async def stop(self):
        if not self._entered:
            return
        self._entered = False
        self._tool_names = set()
        await self.adapter.__aexit__(None, None, None)
