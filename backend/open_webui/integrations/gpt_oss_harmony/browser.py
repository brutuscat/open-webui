"""Request-scoped adapter around OpenAI's unmodified SimpleBrowserTool."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Callable

from open_webui.vendor.openai_gpt_oss_browser.simple_browser.simple_browser_tool import (
    SimpleBrowserTool,
)

from .browser_backend import OpenWebUIBrowserBackend
from .metrics import emit_native_tool_event

MAX_CURSORS = 32
MAX_STATE_BYTES = 20 * 1024 * 1024
MAX_BROWSER_CALLS = 16


class HarmonyBrowser:
    """Serializes native browser calls and keeps state on the active request only."""

    def __init__(self, backend: OpenWebUIBrowserBackend) -> None:
        self.tool = SimpleBrowserTool(backend=backend)
        self.lock = asyncio.Lock()
        self.call_count = 0
        self.citation_snapshots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)

    async def search(self, query: str, topn: int = 10, source: str | None = None) -> str:
        return await self._call(self.tool.search, source=source, query=query, topn=topn)

    async def open(
        self,
        cursor: int = -1,
        id: int | str = -1,
        loc: int = -1,
        num_lines: int = -1,
        view_source: bool = False,
        source: str | None = None,
    ) -> str:
        return await self._call(
            self.tool.open,
            source=source,
            cursor=cursor,
            id=id,
            loc=loc,
            num_lines=num_lines,
            view_source=view_source,
        )

    async def find(self, pattern: str, cursor: int = -1) -> str:
        return await self._call(self.tool.find, pattern=pattern, cursor=cursor)

    async def _call(self, function: Callable[..., AsyncIterator[Any]], **kwargs: Any) -> str:
        started = time.perf_counter()
        tool_name = function.__name__
        backend_metrics = getattr(self.tool.backend, "metrics", {})
        backend_metrics.clear()
        result = ""
        success = False
        # The reference surface accepts ``source`` but this deployment deliberately
        # exposes exactly one backend: OpenWebUI's configured web provider.  Normalise
        # model-supplied source labels rather than reject them and trigger retry loops.
        if "source" in kwargs:
            kwargs["source"] = None
        async with self.lock:
            if len(self.tool.tool_state.page_stack) >= MAX_CURSORS:
                result = f"Error: maximum browser cursor limit ({MAX_CURSORS}) reached."
            elif self.call_count >= MAX_BROWSER_CALLS:
                result = (
                    f"Error: maximum browser call limit ({MAX_BROWSER_CALLS}) reached. "
                    "Do not call the browser again; answer using the sources already retrieved."
                )
            else:
                self.call_count += 1
                messages = [message async for message in function(**kwargs)]
                if self._state_bytes() > MAX_STATE_BYTES:
                    self._discard_last_page()
                    result = "Error: browser response state exceeded its 20 MiB limit."
                else:
                    result = self._message_text(messages[-1]) if messages else "Error: browser returned no result."
        result = self._with_citation_reminder(tool_name, result)
        success = not result.startswith("Error:")
        if success and result:
            self._capture_citation_snapshot(tool_name, result)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        backend_ms = backend_metrics.get("backend_ms", 0.0)
        emit_native_tool_event({
            "namespace": "browser",
            "tool": tool_name,
            "success": success,
            "latency_ms": latency_ms,
            "adapter_ms": round(max(latency_ms - backend_ms, 0.0), 3),
            "backend_ms": backend_ms,
            "input_bytes": len(str(kwargs).encode("utf-8")),
            "output_bytes": len(result.encode("utf-8")),
            "cursor": self.tool.tool_state.current_cursor,
            "cache_hit": False,
        })
        return result

    def _with_citation_reminder(self, tool_name: str, result: str) -> str:
        if tool_name not in {"open", "find"} or result.startswith("Error:"):
            return result

        cursor = self.tool.tool_state.current_cursor
        if cursor < 0:
            return result

        return (
            f"{result}\n\nCitation reminder: cite inspected lines in the final answer as "
            f"【{cursor}†Lstart-Lend】."
        )

    def _capture_citation_snapshot(self, tool_name: str, result: str) -> None:
        if tool_name not in {"search", "open"} or not self.tool.tool_state.page_stack:
            return

        page = self.tool.tool_state.get_page()
        if tool_name == "search":
            snapshot = {
                "document": result[:500],
                "urls": [str(url) for url in page.urls.values()],
            }
        else:
            if not page.url:
                return
            snapshot = {
                "document": str(page.text)[:500],
                "url": str(page.url),
                "title": str(page.title or page.url),
            }
        self.citation_snapshots[tool_name].append(snapshot)

    def pop_citation_snapshot(self, tool_name: str) -> dict[str, Any] | None:
        queue = self.citation_snapshots.get(tool_name.removeprefix("browser."))
        if not queue:
            return None
        return queue.popleft()

    def _state_bytes(self) -> int:
        return sum(len(page.text.encode("utf-8")) for page in self.tool.tool_state.pages.values())

    def _discard_last_page(self) -> None:
        if not self.tool.tool_state.page_stack:
            return
        url = self.tool.tool_state.page_stack.pop()
        if url not in self.tool.tool_state.page_stack:
            self.tool.tool_state.pages.pop(url, None)

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", [])
        if not isinstance(content, list):
            content = [content]
        return "".join(
            str(getattr(item, "text", ""))
            for item in content
            if getattr(item, "text", None) is not None
        )


def get_browser(request: Any, user: dict[str, Any]) -> HarmonyBrowser:
    browser = getattr(request.state, "gpt_oss_browser", None)
    if browser is None:
        browser = HarmonyBrowser(OpenWebUIBrowserBackend(request=request, user=user, metrics={}))
        request.state.gpt_oss_browser = browser
    return browser
