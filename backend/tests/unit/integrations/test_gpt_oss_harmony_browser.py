import unittest
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from open_webui.integrations.gpt_oss_harmony.browser import HarmonyBrowser
from open_webui.integrations.gpt_oss_harmony.browser_backend import (
    BackendError,
    OpenWebUIBrowserBackend,
    VIEW_SOURCE_PREFIX,
)
from open_webui.integrations.gpt_oss_harmony.citations import browser_citation_sources
from open_webui.integrations.gpt_oss_harmony.detection import (
    CAPABILITY,
    enable_native_namespaces,
    is_native_harmony_model,
)
from open_webui.integrations.gpt_oss_harmony.dispatch import native_browser_tools
from open_webui.integrations.gpt_oss_harmony.terminal import native_container_tools
from open_webui.vendor.openai_gpt_oss_browser.simple_browser.page_contents import PageContents
from open_webui.utils.tools import get_builtin_tools


class Backend:
    source = "web"

    async def search(self, query, topn, session):
        del query, topn, session
        return PageContents(
            url="",
            title="release notes",
            text="【0†Example】\nA result.",
            urls={"0": "https://example.test/article"},
            snippets={},
        )

    async def fetch(self, url, session):
        del session
        return PageContents(
            url=url,
            title="Example",
            text="first line\nrelease note\nlast line",
            urls={},
        )


class HarmonyBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_search_selects_native_browser_only_for_explicit_model(self):
        config = {"web.search.enable": True}
        native_tools = {"browser.search": {"spec": {"name": "browser.search"}}}
        model = {
            "info": {
                "meta": {
                    "capabilities": {"web_search": True, CAPABILITY: True},
                    "builtinTools": {"web_search": True},
                }
            }
        }
        with patch(
            "open_webui.utils.tools.Config.get_many", new=AsyncMock(return_value=config)
        ), patch(
            "open_webui.utils.tools.has_permission", new=AsyncMock(return_value=True)
        ), patch(
            "open_webui.utils.tools.native_browser_tools", return_value=native_tools
        ) as browser_tools:
            tools = await get_builtin_tools(
                SimpleNamespace(state=SimpleNamespace()), {"__user__": {"id": "user-1"}}, {"web_search": True}, model
            )

        self.assertEqual(tools["browser.search"], native_tools["browser.search"])
        self.assertFalse({"search_web", "fetch_url"}.intersection(tools))
        browser_tools.assert_called_once()

    async def test_web_search_keeps_generic_tools_for_ordinary_model(self):
        config = {"web.search.enable": True}
        model = {
            "info": {
                "meta": {
                    "capabilities": {"web_search": True},
                    "builtinTools": {"web_search": True},
                }
            }
        }
        with patch(
            "open_webui.utils.tools.Config.get_many", new=AsyncMock(return_value=config)
        ), patch(
            "open_webui.utils.tools.has_permission", new=AsyncMock(return_value=True)
        ), patch("open_webui.utils.tools.native_browser_tools") as browser_tools:
            tools = await get_builtin_tools(
                SimpleNamespace(state=SimpleNamespace()), {"__user__": {"id": "user-1"}}, {"web_search": True}, model
            )

        self.assertTrue({"search_web", "fetch_url"}.issubset(tools))
        browser_tools.assert_not_called()

    async def test_web_search_off_hides_native_browser(self):
        model = {
            "info": {
                "meta": {
                    "capabilities": {"web_search": True, CAPABILITY: True},
                    "builtinTools": {"web_search": True},
                }
            }
        }
        with patch(
            "open_webui.utils.tools.Config.get_many",
            new=AsyncMock(return_value={"web.search.enable": True}),
        ), patch("open_webui.utils.tools.native_browser_tools") as browser_tools:
            tools = await get_builtin_tools(
                SimpleNamespace(state=SimpleNamespace()),
                {"__user__": {"id": "user-1"}},
                {"web_search": False},
                model,
            )

        self.assertNotIn("browser.search", tools)
        browser_tools.assert_not_called()

    async def test_search_open_find_uses_reference_cursor_state(self):
        browser = HarmonyBrowser(Backend())
        search = await browser.search("release notes")
        self.assertIn("【0†Example】", search)

        opened = await browser.open(cursor=0, id=0, loc=1, num_lines=1)
        self.assertIn("[1]", opened)
        self.assertIn("L1: release note", opened)

        found = await browser.find("release", cursor=1)
        self.assertIn("match at L1", found)

    def test_capability_is_explicit(self):
        self.assertFalse(is_native_harmony_model({"id": "gpt-oss-20b"}))
        self.assertTrue(
            is_native_harmony_model(
                {"info": {"meta": {"capabilities": {"gpt_oss_harmony_native_tools": True}}}}
            )
        )

    def test_native_namespaces_preserve_template_options(self):
        form_data = {"chat_template_kwargs": {"reasoning_effort": "medium"}}
        enable_native_namespaces(form_data, {"container", "browser"})
        self.assertEqual(
            form_data["chat_template_kwargs"],
            {"reasoning_effort": "medium", "builtin_tools": ["browser", "container"]},
        )

    def test_native_dispatch_entries_are_not_generic_tools(self):
        tools = native_browser_tools(SimpleNamespace(state=SimpleNamespace()), {})
        self.assertEqual(set(tools), {"browser.search", "browser.open", "browser.find"})
        self.assertTrue(all(tool["native_harmony_hidden"] for tool in tools.values()))

    async def test_container_exec_reuses_terminal_runner(self):
        seen = []

        async def run_command(command):
            seen.append(command)
            return {"stdout": "ok", "exit_code": 0}

        tools = native_container_tools({"run_command": {"tool_id": "terminal:chosen", "callable": run_command}})
        result = await tools["container.exec"]["callable"](command=["bash", "-lc", "printf 'ok'"])
        self.assertEqual(seen, [shlex.join(["bash", "-lc", "printf 'ok'"])])
        self.assertIn('"exit_code":0', result.replace(" ", ""))
        self.assertEqual(native_container_tools({}), {})

    async def test_container_exec_tolerates_aliases_without_advertising_them(self):
        seen = []

        async def run_command(command):
            seen.append(command)
            return "completed"

        tool = native_container_tools({"run_command": {"callable": run_command}})["container.exec"]
        self.assertEqual(tool["spec"]["parameters"]["required"], ["command"])
        self.assertEqual(await tool["callable"](cmd=["bash", "-lc", "printf hello"]), "completed")
        self.assertEqual(await tool["callable"](commands="pwd"), "completed")
        self.assertEqual(seen, ["bash -lc 'printf hello'", "pwd"])
        self.assertIn("requires command", await tool["callable"]())
        self.assertIn("non-empty", await tool["callable"](command=[]))

    async def test_browser_results_emit_source_cards(self):
        browser = HarmonyBrowser(Backend())
        request = SimpleNamespace(state=SimpleNamespace(gpt_oss_browser=browser))
        search = await browser.search("release notes")
        search_sources = browser_citation_sources(request, "browser.search", search)
        self.assertEqual(search_sources[0]["metadata"][0]["url"], "https://example.test/article")

        opened = await browser.open(cursor=0, id=0)
        open_sources = browser_citation_sources(request, "browser.open", opened)
        self.assertEqual(open_sources[0]["metadata"][0]["url"], "https://example.test/article")

    async def test_openwebui_backend_reuses_search_and_fetch_tools(self):
        request = SimpleNamespace()
        user = {"id": "user-1"}
        backend = OpenWebUIBrowserBackend(request=request, user=user, metrics={})
        search_result = '[{"title":"Example","link":"https://example.test/a","snippet":"Snippet"}]'
        with patch(
            "open_webui.integrations.gpt_oss_harmony.browser_backend.search_web",
            new=AsyncMock(return_value=search_result),
        ) as search_web, patch(
            "open_webui.integrations.gpt_oss_harmony.browser_backend.fetch_url",
            new=AsyncMock(return_value="Extracted page"),
        ) as fetch_url:
            search = await backend.search("release notes", 3, None)
            page = await backend.fetch(VIEW_SOURCE_PREFIX + "https://example.test/a", None)

        search_web.assert_awaited_once_with(
            query="release notes", count=3, __request__=request, __user__=user
        )
        fetch_url.assert_awaited_once_with(
            url="https://example.test/a", __request__=request, __user__=user
        )
        self.assertEqual(search.urls, {"0": "https://example.test/a"})
        self.assertIn("【0†Example】", search.text)
        self.assertEqual(page.text, "Extracted page")
        self.assertGreaterEqual(backend.metrics["backend_ms"], 0)

    async def test_openwebui_backend_surfaces_existing_tool_errors(self):
        backend = OpenWebUIBrowserBackend(request=SimpleNamespace(), user={}, metrics={})
        with patch(
            "open_webui.integrations.gpt_oss_harmony.browser_backend.search_web",
            new=AsyncMock(return_value='{"error":"provider unavailable"}'),
        ):
            with self.assertRaisesRegex(BackendError, "provider unavailable"):
                await backend.search("release notes", 3, None)
