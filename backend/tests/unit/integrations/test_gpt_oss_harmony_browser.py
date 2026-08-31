import unittest
from types import SimpleNamespace

from open_webui.integrations.gpt_oss_harmony.browser import HarmonyBrowser
from open_webui.integrations.gpt_oss_harmony.detection import is_native_harmony_model
from open_webui.integrations.gpt_oss_harmony.dispatch import native_browser_tools
from open_webui.vendor.openai_gpt_oss_browser.simple_browser.page_contents import PageContents


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

    def test_native_dispatch_entries_are_not_generic_tools(self):
        tools = native_browser_tools(SimpleNamespace(state=SimpleNamespace()), {})
        self.assertEqual(set(tools), {"browser.search", "browser.open", "browser.find"})
        self.assertTrue(all(tool["native_harmony_hidden"] for tool in tools.values()))
