"""Translate request-scoped browser pages into ordinary OpenWebUI source cards."""

from __future__ import annotations

from typing import Any


def browser_citation_sources(request: Any, tool_name: str, tool_result: str) -> list[dict]:
    browser = getattr(request.state, "gpt_oss_browser", None)
    if browser is None or not browser.tool.tool_state.page_stack:
        return []
    page = browser.tool.tool_state.get_page()
    if tool_name == "browser.search":
        return [
            {
                "source": {"name": "browser.search", "id": "browser.search"},
                "document": [tool_result[:500]],
                "metadata": [
                    {"source": url, "name": url, "url": url}
                    for url in page.urls.values()
                ],
            }
        ]
    if tool_name == "browser.open" and page.url:
        return [
            {
                "source": {"name": page.title or page.url, "id": page.url},
                "document": [page.text[:500]],
                "metadata": [
                    {"source": page.url, "name": page.title or page.url, "url": page.url}
                ],
            }
        ]
    return []
