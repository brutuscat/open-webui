"""Bridge the vendored browser backend contract to OpenWebUI web facilities."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientSession
import chz

from open_webui.tools.builtin import fetch_url, search_web
from open_webui.utils.json_codec import JSONCodec
from open_webui.vendor.openai_gpt_oss_browser.simple_browser.backend import (
    VIEW_SOURCE_PREFIX,
    Backend,
    BackendError,
)
from open_webui.vendor.openai_gpt_oss_browser.simple_browser.page_contents import (
    Extract,
    PageContents,
)


@chz.chz(typecheck=True)
class OpenWebUIBrowserBackend(Backend):
    """Reference-browser backend with no independent network client."""

    source: str = "web"
    request: Any = chz.field(repr=False)
    user: dict[str, Any] = chz.field(repr=False)

    async def search(
        self, query: str, topn: int, session: ClientSession
    ) -> PageContents:
        del session
        raw = await search_web(
            query=query,
            count=topn,
            __request__=self.request,
            __user__=self.user,
        )
        payload = JSONCodec.loads(raw)
        if isinstance(payload, dict) and payload.get("error"):
            raise BackendError(str(payload["error"]))
        if not isinstance(payload, list):
            return PageContents(url="", title=query, text="", urls={}, snippets={})

        results = [item for item in payload if isinstance(item, dict) and item.get("link")]
        urls = {str(index): str(item["link"]) for index, item in enumerate(results)}
        snippets = {
            str(index): Extract(
                url=str(item["link"]),
                title=str(item.get("title") or item["link"]),
                text=str(item.get("snippet") or ""),
            )
            for index, item in enumerate(results)
        }
        text = "\n\n".join(
            "【{index}†{title}】\n{snippet}".format(
                index=index,
                title=str(item.get("title") or item["link"]),
                snippet=str(item.get("snippet") or ""),
            )
            for index, item in enumerate(results)
        )
        return PageContents(url="", title=query, text=text, urls=urls, snippets=snippets)

    async def fetch(self, url: str, session: ClientSession) -> PageContents:
        del session
        if url.startswith(VIEW_SOURCE_PREFIX):
            url = url[len(VIEW_SOURCE_PREFIX) :]
        content = await fetch_url(url=url, __request__=self.request, __user__=self.user)
        if not isinstance(content, str):
            raise BackendError("OpenWebUI URL loader returned a non-text response")
        try:
            payload = JSONCodec.loads(content)
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            raise BackendError(str(payload["error"]))
        return PageContents(url=url, title=url, text=content, urls={})
