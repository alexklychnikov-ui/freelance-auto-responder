from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

DEFAULT_BROWSERMCP_SERVER = (
    r"C:\Python\Projects\BrowserMCP\packages\mcp-server\dist\index.js"
)


class BrowserMcpError(RuntimeError):
    pass


class BrowserMcpSession:
    """Persistent stdio MCP session to BrowserMCP node server (thread-safe sync API)."""

    def __init__(
        self,
        server_path: str,
        *,
        node_command: str = "node",
        navigate_wait_seconds: float = 2.0,
    ) -> None:
        self.server_path = server_path
        self.node_command = node_command
        self.navigate_wait_seconds = navigate_wait_seconds
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run_loop,
                name="browser-mcp",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=60):
            raise BrowserMcpError("BrowserMCP session start timeout (60s)")
        if self._start_error is not None:
            raise BrowserMcpError(f"BrowserMCP session failed: {self._start_error}") from self._start_error

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
        finally:
            self._loop.close()

    async def _main(self) -> None:
        self._stop_event = asyncio.Event()
        try:
            await self._connect()
            self._ready.set()
            await self._stop_event.wait()
        finally:
            await self._disconnect()

    async def _connect(self) -> None:
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self.node_command,
            args=[self.server_path],
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        logger.info("BrowserMCP connected via %s %s", self.node_command, self.server_path)

    async def _disconnect(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            stop_event = self._stop_event
            thread = self._thread
        if loop is not None and stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)
        if thread is not None:
            thread.join(timeout=15)
        logger.info("BrowserMCP session closed")

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.start()
        if self._loop is None or self._session is None:
            raise BrowserMcpError("BrowserMCP session not available")
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(name, arguments or {}),
            self._loop,
        )
        return future.result(timeout=120)

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise BrowserMcpError("BrowserMCP session not connected")
        result = await self._session.call_tool(name, arguments)
        if result.isError:
            message = _extract_text(result.content) or "unknown MCP tool error"
            raise BrowserMcpError(f"{name} failed: {message}")
        return result.content

    def navigate(self, url: str) -> None:
        self.call_tool("browser_navigate", {"url": url})
        if self.navigate_wait_seconds > 0:
            self.call_tool("browser_wait", {"time": self.navigate_wait_seconds})

    def snapshot(self) -> str:
        content = self.call_tool("browser_snapshot", {})
        return _extract_text(content)

    def evaluate(self, js: str) -> Any:
        content = self.call_tool(
            "browser_cdp",
            {
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            },
        )
        text = _extract_text(content)
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            if payload.get("exceptionDetails"):
                raise BrowserMcpError(f"Runtime.evaluate exception: {payload['exceptionDetails']}")
            result = payload.get("result", {})
            return result.get("value", result)
        return payload

    def click(self, ref_or_selector: str) -> None:
        if _looks_like_snapshot_ref(ref_or_selector):
            self.call_tool(
                "browser_click",
                {"element": ref_or_selector, "ref": ref_or_selector},
            )
            return
        selector = json.dumps(ref_or_selector)
        self.evaluate(
            f"""(() => {{
  const el = document.querySelector({selector});
  if (!el) throw new Error('Element not found: ' + {selector});
  el.click();
  return true;
}})()"""
        )

    def fill(self, ref_or_selector: str, text: str) -> None:
        if _looks_like_snapshot_ref(ref_or_selector):
            self.call_tool(
                "browser_fill",
                {
                    "element": ref_or_selector,
                    "ref": ref_or_selector,
                    "value": text,
                },
            )
            return
        selector = json.dumps(ref_or_selector)
        value = json.dumps(text)
        self.evaluate(
            f"""(() => {{
  const el = document.querySelector({selector});
  if (!el) throw new Error('Element not found: ' + {selector});
  el.focus();
  if ('value' in el) el.value = {value};
  else el.textContent = {value};
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return true;
}})()"""
        )

    def screenshot(self) -> bytes:
        content = self.call_tool("browser_screenshot", {})
        for block in content:
            if getattr(block, "type", None) == "image":
                import base64

                data = getattr(block, "data", None)
                if data:
                    return base64.b64decode(data)
        raise BrowserMcpError("browser_screenshot returned no image data")


def _extract_text(content: Any) -> str:
    parts: list[str] = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _looks_like_snapshot_ref(value: str) -> bool:
    return value.startswith("ref-") or value.startswith("@")


def resolve_server_path(path: str | None) -> str:
    if path and path.strip():
        return path.strip()
    return DEFAULT_BROWSERMCP_SERVER
