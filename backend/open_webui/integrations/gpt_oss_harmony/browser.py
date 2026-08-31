"""Request-scoped adapter around OpenAI's unmodified SimpleBrowserTool."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

from open_webui.vendor.openai_gpt_oss_browser.simple_browser.simple_browser_tool import (
    SimpleBrowserTool,
)

from .browser_backend import OpenWebUIBrowserBackend

MAX_CURSORS = 32
MAX_STATE_BYTES = 20 * 1024 * 1024


class HarmonyBrowser:
    """Serializes native browser calls and keeps state on the active request only."""

    def __init__(self, backend: OpenWebUIBrowserBackend) -> None:
        self.tool = SimpleBrowserTool(backend=backend)
        self.lock = asyncio.Lock()

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
        if kwargs.get("source") not in (None, "", "web"):
            return "Error: only the configured OpenWebUI web source is available."
        async with self.lock:
            if len(self.tool.tool_state.page_stack) >= MAX_CURSORS:
                return f"Error: maximum browser cursor limit ({MAX_CURSORS}) reached."
            messages = [message async for message in function(**kwargs)]
            if self._state_bytes() > MAX_STATE_BYTES:
                self._discard_last_page()
                return "Error: browser response state exceeded its 20 MiB limit."
        return self._message_text(messages[-1]) if messages else "Error: browser returned no result."

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
        browser = HarmonyBrowser(OpenWebUIBrowserBackend(request=request, user=user))
        request.state.gpt_oss_browser = browser
    return browser
