"""不依赖浏览器的纯逻辑单元测试。"""

from __future__ import annotations

import json
import os
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qa_automation.browser import _chrome_executable
from qa_automation.mcp.metrics import _size
from qa_automation.overlay.enrichment import _dedupe_overlays, _new_overlays
from qa_automation.overlay.scripts import _OVERLAY_DEADLINE_VAR, _overlay_arm_script
from qa_automation.profiles import active_profile
from qa_automation.workspace import safe_filename


class DedupeOverlayTests(unittest.TestCase):
    def test_same_fingerprint_keeps_latest_visible(self) -> None:
        base = {"frame_id": "f0", "fingerprint": "a", "kind": "dialog", "text": "Hi"}
        older = {**base, "visible": False, "timestamp": 1}
        newer = {**base, "visible": True, "timestamp": 2}

        result = _dedupe_overlays([older, newer])

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["visible"])

    def test_removed_event_supersedes_visible(self) -> None:
        base = {"frame_id": "f0", "fingerprint": "a", "kind": "message", "text": "x"}
        visible = {**base, "visible": True, "timestamp": 5}
        removed = {**base, "visible": False, "timestamp": 6, "event": "removed"}

        result = _dedupe_overlays([visible, removed])

        self.assertEqual(result[-1].get("event"), "removed")

    def test_new_overlays_excludes_baseline_fingerprints(self) -> None:
        baseline = [{"frame_id": "f0", "fingerprint": "a"}]
        events = [
            {"frame_id": "f0", "fingerprint": "a"},
            {"frame_id": "f0", "fingerprint": "b"},
        ]

        result = _new_overlays(baseline, events, [])

        self.assertEqual([item["fingerprint"] for item in result], ["b"])


class SafeFilenameTests(unittest.TestCase):
    def test_strips_path_and_invalid_chars(self) -> None:
        self.assertEqual(safe_filename("..\\evil<>.png", fallback="x"), "evil__.png")

    def test_reserved_names_get_prefix(self) -> None:
        self.assertEqual(safe_filename("CON", fallback="x"), "_CON")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(safe_filename("   ", fallback="fallback.png"), "fallback.png")


class SizeEstimatorTests(unittest.TestCase):
    def test_ascii_string_counts_bytes_without_copy(self) -> None:
        big = "A" * 100_000
        self.assertEqual(_size(big), 100_002)

    def test_nested_structure_is_positive_and_monotonic(self) -> None:
        small = {"a": [1, 2], "b": "x"}
        large = {"a": [1, 2, 3, 4], "b": "xy", "c": None}
        self.assertGreater(_size(large), _size(small))


class ArmScriptTests(unittest.TestCase):
    def test_deadline_read_from_window_variable_with_parent_fallback(self) -> None:
        script = _overlay_arm_script()
        # deadline 从 window 变量读取(而非内联字面量),动态 iframe 沿 parent 链查找
        self.assertIn(f".{_OVERLAY_DEADLINE_VAR}", script)
        self.assertIn("w.parent", script)


class ChromeExecutableTests(unittest.TestCase):
    def test_raises_when_nothing_found(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "qa_automation.browser.shutil.which", return_value=None
        ), patch("os.path.isfile", return_value=False):
            with self.assertRaises(RuntimeError):
                _chrome_executable()

    def test_prefers_explicit_existing_path(self) -> None:
        with patch("os.path.isfile", side_effect=lambda p: p == "C:/custom/chrome.exe"):
            self.assertEqual(_chrome_executable("C:/custom/chrome.exe"), "C:/custom/chrome.exe")


class BrowserStateTests(unittest.TestCase):
    def test_reset_clears_lifecycle_but_keeps_id_counters(self) -> None:
        from qa_automation.browser import _BrowserState

        state = _BrowserState()
        state.browser = object()
        state.cdp = True
        state.selected_page = object()
        state.page_id_counter = 7
        state.context_id_counter = 3
        state.context_names["session-default"] = "default"
        state.owned_contexts["session-default"] = object()
        state.download_failures.append("boom")

        state.reset()

        self.assertIsNone(state.browser)
        self.assertFalse(state.cdp)
        self.assertIsNone(state.selected_page)
        # id 计数器保留,保证 page-N/session-N 跨会话唯一
        self.assertEqual(state.page_id_counter, 7)
        self.assertEqual(state.context_id_counter, 3)
        self.assertEqual(state.context_names, {})
        self.assertEqual(state.owned_contexts, {})
        self.assertEqual(state.download_failures, [])

    def test_reset_chrome_clears_process_fields(self) -> None:
        from qa_automation.browser import _BrowserState

        state = _BrowserState()
        state.chrome_process = object()
        state.chrome_port = 9222
        state.chrome_profile = "C:/tmp/profile"
        state.chrome_profile_owned = True

        state.reset_chrome()

        self.assertIsNone(state.chrome_process)
        self.assertIsNone(state.chrome_port)
        self.assertIsNone(state.chrome_profile)
        self.assertFalse(state.chrome_profile_owned)


class _FakeWatchable:
    """可 weakref 的普通对象,模拟 Page/Context。"""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.listeners: list[str] = []

    def on(self, event: str, cb: object) -> None:
        self.listeners.append(event)


class _SlottedFake:
    """__slots__ 且无 __weakref__:不可 weakref,触发 id() 兜底路径。"""

    __slots__ = ("name", "listeners", "main_frame")

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.listeners: list[str] = []
        self.main_frame: object = object()  # 占位,测试中可覆写

    def on(self, event: str, cb: object) -> None:
        self.listeners.append(event)


class DownloadWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        from qa_automation import browser

        self.browser = browser
        self.browser._state.download_pages.clear()
        self.browser._state.download_contexts.clear()
        self.browser._state.download_page_ids.clear()
        self.browser._state.download_context_ids.clear()
        self.addCleanup(self.browser._state.download_pages.clear)
        self.addCleanup(self.browser._state.download_contexts.clear)
        self.addCleanup(self.browser._state.download_page_ids.clear)
        self.addCleanup(self.browser._state.download_context_ids.clear)

    def test_same_page_registered_exactly_once(self) -> None:
        page = _FakeWatchable()
        self.browser._watch_download_page(page)
        self.browser._watch_download_page(page)
        self.assertEqual(page.listeners, ["download"])

    def test_weakset_drops_collected_pages(self) -> None:
        import gc

        page = _FakeWatchable()
        self.browser._watch_download_page(page)
        self.assertEqual(len(self.browser._state.download_pages), 1)
        del page
        gc.collect()
        # 弱引用语义:对象回收后条目消失,地址复用不会再误判"已注册"
        self.assertEqual(len(self.browser._state.download_pages), 0)

    def test_unweakrefable_page_falls_back_to_id_dedup(self) -> None:
        page = _SlottedFake()
        self.browser._watch_download_page(page)
        self.browser._watch_download_page(page)
        self.assertEqual(page.listeners, ["download"])
        self.assertEqual(self.browser._state.download_page_ids, {id(page)})

    def test_context_watch_dedupes_and_registers_children(self) -> None:
        context = _FakeWatchable()
        child = _FakeWatchable()
        context.pages = [child]  # type: ignore[attr-defined]
        self.browser._watch_download_context(context)
        self.browser._watch_download_context(context)
        self.assertEqual(context.listeners, ["page"])
        self.assertEqual(child.listeners, ["download"])


class FallbackRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        from qa_automation import browser

        self.browser = browser
        for registry in (
            self.browser._state.fallback_frame_ids,
            self.browser._state.fallback_frame_counters,
            self.browser._state.fallback_context_ids,
        ):
            registry.clear()
            self.addCleanup(registry.clear)

    @staticmethod
    def _slotted_frame() -> _SlottedFake:
        return _SlottedFake()

    def test_frame_id_stable_for_same_unweakrefable_pair(self) -> None:
        page = _SlottedFake("page")
        page.main_frame = _SlottedFake("main")  # type: ignore[attr-defined]
        frame = _SlottedFake("frame")
        first = self.browser._frame_id(page, frame)
        second = self.browser._frame_id(page, frame)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("frame-object-"))
        other = self.browser._frame_id(page, self._slotted_frame())
        self.assertNotEqual(first, other)

    def test_fallback_registries_are_bounded(self) -> None:
        with patch("qa_automation.browser._FALLBACK_REGISTRY_LIMIT", 4):
            page = _SlottedFake("page")
            page.main_frame = _SlottedFake("main")  # type: ignore[attr-defined]
            for _ in range(20):
                self.browser._frame_id(page, self._slotted_frame())
        self.assertLessEqual(len(self.browser._state.fallback_frame_ids), 4)
        self.assertLessEqual(len(self.browser._state.fallback_frame_counters), 4)

    def test_prune_drops_oldest_half_only_when_over_limit(self) -> None:
        from qa_automation.browser import _prune_fallback_registry

        registry = {i: i for i in range(10)}
        _prune_fallback_registry(registry)
        self.assertEqual(len(registry), 10)  # 低于上限不动

        with patch("qa_automation.browser._FALLBACK_REGISTRY_LIMIT", 4):
            _prune_fallback_registry(registry)
        self.assertEqual(len(registry), 5)  # 10 -> 丢最旧 5 条
        self.assertEqual(sorted(registry), [5, 6, 7, 8, 9])


class _FakeUrlopenResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeUrlopenResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class CdpProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_probe_accepts_valid_cdp_endpoint(self) -> None:
        from qa_automation import browser

        resp = _FakeUrlopenResponse(200, json.dumps({"Browser": "Chrome/120"}).encode())
        with patch("urllib.request.urlopen", return_value=resp):
            payload = browser._probe_cdp(9222)
        self.assertEqual(payload["Browser"], "Chrome/120")

    def test_probe_flags_non_cdp_html_response(self) -> None:
        from qa_automation import browser
        from qa_automation.browser import _PortHeldByOtherService

        resp = _FakeUrlopenResponse(200, b"<html>welcome to nginx</html>")
        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(_PortHeldByOtherService):
                browser._probe_cdp(9222)

    def test_probe_flags_json_without_browser_field(self) -> None:
        from qa_automation import browser
        from qa_automation.browser import _PortHeldByOtherService

        resp = _FakeUrlopenResponse(200, b'{"status": "ok"}')
        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaises(_PortHeldByOtherService):
                browser._probe_cdp(9222)

    async def test_wait_for_cdp_fails_fast_when_process_exits(self) -> None:
        from qa_automation import browser

        proc = SimpleNamespace(poll=lambda: 1, returncode=1)
        with self.assertRaisesRegex(RuntimeError, "立即退出"):
            await browser._wait_for_cdp(9222, 60_000, proc=proc)

    async def test_wait_for_cdp_fails_fast_on_non_cdp_service(self) -> None:
        from qa_automation import browser

        resp = _FakeUrlopenResponse(200, b"{}")
        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "非 Chrome CDP 服务占用"):
                await browser._wait_for_cdp(9222, 60_000, proc=None)

    async def test_wait_for_cdp_returns_url_when_ready(self) -> None:
        from qa_automation import browser

        resp = _FakeUrlopenResponse(200, json.dumps({"Browser": "Chrome/120"}).encode())
        with patch("urllib.request.urlopen", return_value=resp):
            url = await browser._wait_for_cdp(9222, 60_000, proc=None)
        self.assertEqual(url, "http://127.0.0.1:9222")

    async def test_launch_refuses_when_port_already_serving_cdp(self) -> None:
        from qa_automation import browser

        with (
            patch.object(browser._state, "chrome_process", None),
            patch.object(browser, "_probe_cdp", return_value={"Browser": "Chrome/120"}),
            patch("subprocess.Popen") as popen,
        ):
            with self.assertRaisesRegex(RuntimeError, "browser_connect"):
                await browser._launch_chrome_impl(port=9222)
        popen.assert_not_called()

    async def test_launch_refuses_when_port_held_by_non_cdp_service(self) -> None:
        from qa_automation import browser
        from qa_automation.browser import _PortHeldByOtherService

        with (
            patch.object(browser._state, "chrome_process", None),
            patch.object(browser, "_probe_cdp", side_effect=_PortHeldByOtherService("nginx")),
            patch("subprocess.Popen") as popen,
        ):
            with self.assertRaisesRegex(RuntimeError, "非 Chrome CDP 服务占用"):
                await browser._launch_chrome_impl(port=9222)
        popen.assert_not_called()


class TencentDocsTokenTests(unittest.TestCase):
    def test_token_requires_explicit_configuration(self) -> None:
        from qa_automation.config import resolve_tencent_docs_token

        with patch.dict(os.environ, {}, clear=True), patch(
            "qa_automation.config.Path.home", return_value=Path("C:/qa-empty-home")
        ), patch("qa_automation.config.Path.exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "TENCENT_DOCS_MCP_TOKEN"):
                resolve_tencent_docs_token()

    def test_token_reads_environment_variable(self) -> None:
        from qa_automation.config import resolve_tencent_docs_token

        with patch.dict(os.environ, {"TENCENT_DOCS_MCP_TOKEN": " test-token "}):
            self.assertEqual(resolve_tencent_docs_token(), "test-token")


class ProfileFallbackTests(unittest.TestCase):
    def test_unknown_profile_falls_back_with_warning(self) -> None:
        with patch.dict(os.environ, {"QA_AUTOMATION_PROFILE": "no-such-profile"}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                profile = active_profile()
        self.assertEqual(profile.name, "aps-antd")
        self.assertTrue(any("no-such-profile" in str(item.message) for item in caught))


class ViewportHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_viewport_clears_metrics_and_returns_dims(self) -> None:
        from unittest.mock import AsyncMock

        import qa_automation.browser as b

        mock_page = SimpleNamespace(is_closed=lambda: False)
        with patch.object(b, "_current_page_impl", AsyncMock(return_value=mock_page)), \
             patch.object(b, "_maximize_and_fill_viewport", AsyncMock()) as mock_fill, \
             patch.object(b, "_page_viewport_size", AsyncMock(return_value={"width": 1920, "height": 1080})), \
             patch.object(b, "_page_id", return_value="page_test_1"):
            res = await b.reset_viewport()

        mock_fill.assert_awaited_once_with(mock_page)
        self.assertEqual(res["status"], "viewport-reset")
        self.assertEqual(res["page_id"], "page_test_1")
        self.assertEqual(res["viewport"], {"width": 1920, "height": 1080})

    async def test_browser_session_accepts_reset_viewport_action(self) -> None:
        from unittest.mock import AsyncMock

        import qa_automation.browser as b

        with patch.object(b, "_reset_viewport_impl", AsyncMock(return_value={"status": "viewport-reset"})) as mock_impl:
            res = await b.browser_session(action="reset_viewport")

        mock_impl.assert_awaited_once()
        self.assertEqual(res["status"], "viewport-reset")

    async def test_maximize_and_fill_viewport_safe_on_mock_or_closed(self) -> None:
        import qa_automation.browser as b

        # None page
        await b._maximize_and_fill_viewport(None)

        # Closed page
        closed_page = SimpleNamespace(is_closed=lambda: True)
        await b._maximize_and_fill_viewport(closed_page)

        # Page without cdp session capability
        plain_page = SimpleNamespace(is_closed=lambda: False)
        await b._maximize_and_fill_viewport(plain_page)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
