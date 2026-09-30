"""Translate request-scoped browser pages into ordinary OpenWebUI source cards."""

from __future__ import annotations

from typing import Any


def browser_citation_sources(request: Any, tool_name: str, tool_result: str) -> list[dict]:
    browser = getattr(request.state, "gpt_oss_browser", None)
    if browser is None:
        return []

    snapshot = browser.pop_citation_snapshot(tool_name)
    if snapshot is None:
        return []

    if tool_name == "browser.search":
        urls = snapshot.get("urls", [])
        return [
            {
                "source": {"name": "browser.search", "id": "browser.search"},
                "document": [str(snapshot.get("document", tool_result[:500]))],
                "metadata": [
                    {"source": url, "name": url, "url": url}
                    for url in urls
                ],
            }
        ]
    if tool_name == "browser.open":
        url = snapshot.get("url")
        if not url:
            return []
        title = str(snapshot.get("title") or url)
        return [
            {
                "source": {"name": title, "id": url},
                "document": [str(snapshot.get("document", ""))],
                "metadata": [
                    {"source": url, "name": title, "url": url}
                ],
            }
        ]
    return []
