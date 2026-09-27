import copy
from typing import Any

from langchain.mcp import MCPAdapter
from langchain_core.tools import BaseTool


PLAYWRIGHT_MCP_URL = "http://localhost:8931/mcp"

# Schemas OpenAI cannot accept even after sanitization (free-form maps, etc.).
_OPENAI_INCOMPATIBLE_TOOLS = frozenset({"browser_drop"})

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


def _openai_compatible_tools(tools: list[BaseTool]) -> list[BaseTool]:
    compatible: list[BaseTool] = []
    for tool in tools:
        if tool.name in _OPENAI_INCOMPATIBLE_TOOLS:
            continue
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            tool.args_schema = _sanitize_schema(copy.deepcopy(schema))
        compatible.append(tool)
    return compatible


class PlaywrightMCP:
    """Client for a standalone Playwright MCP HTTP server.

    Start the server in headed mode (visible browser) in another terminal:

        npx @playwright/mcp@latest --port 8931


    Keep one PlaywrightMCP session open for the whole agent run so page state survives.
    """

    def __init__(self, url: str = PLAYWRIGHT_MCP_URL):
        self.url = url
        self.adapter = MCPAdapter(url)
        self.tools = None
        self._entered = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

    async def start(self):
        try:
            await self.adapter.__aenter__()
            self._entered = True
        except OSError as exc:
            raise ConnectionError(
                f"Playwright MCP is not reachable at {self.url}. "
                "Start it with: npx @playwright/mcp@latest --port 8931"
            ) from exc

        raw_tools = await self.adapter.list_tools()
        self.tools = _openai_compatible_tools(raw_tools)
        return self.tools

    async def stop(self):
        if not self._entered:
            return
        self._entered = False
        await self.adapter.__aexit__(None, None, None)
