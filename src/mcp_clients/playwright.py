from langchain.mcp import MCPAdapter


PLAYWRIGHT_MCP_URL = "http://localhost:8931/mcp"


class PlaywrightMCP:
    """Client for a standalone Playwright MCP HTTP server.

    Start the server in headed mode (visible browser) in another terminal:

        npx @playwright/mcp@latest --port 8931

    There is no open-browser tool. Calling browser_navigate launches the browser.
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

        self.tools = await self.adapter.list_tools()
        return self.tools

    async def stop(self):
        if not self._entered:
            return
        self._entered = False
        await self.adapter.__aexit__(None, None, None)
