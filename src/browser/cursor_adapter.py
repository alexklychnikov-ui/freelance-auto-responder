from __future__ import annotations

from src.browser.mcp_adapter import McpBrowserAdapter


class CursorBrowserAdapter(McpBrowserAdapter):
    """Uses BrowserMCP server — Python daemon cannot call cursor-ide-browser directly."""

    pass
