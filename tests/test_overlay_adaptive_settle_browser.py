"""Real-browser verification that adaptive settling converges without losing overlays.

与 test_overlay_adaptive_settle.py 的分工:
  * 那个文件用 mock 覆盖分支与回退;
  * 本文件在真实渲染引擎里验证"提前收口"的物理正确性——尤其是
    收敛提前返回时,弹窗事件必须已经被 MutationObserver 捕获(不能因为收口早而丢事件)。

浏览器自备:优先 Playwright 自带 chromium,其次系统 Chrome/Edge;
全都不可用时按既有约定 SkipTest,而不是把环境问题伪装成用例失败。
"""

from __future__ import annotations

import os
import time
import unittest

from qa_automation.overlay import (
    _await_overlay_settle,
    _drain_overlay_observers,
    _install_overlay_observers,
)

MODAL_PAGE = """
<!doctype html><html><body>
  <button id="open" style="position:absolute;left:10px;top:10px;width:120px;height:36px">Open</button>
  <script>
    // 拟真 antd 弹窗:延迟出现 + 出现后连续进场动画改写 DOM(触发 attribute mutations)
    document.querySelector('#open').addEventListener('click', () => {
      setTimeout(() => {
        const root = document.createElement('div');
        root.className = 'ant-modal-root';
        root.innerHTML = '<div class="ant-modal" role="dialog" style="position:fixed;left:100px;top:80px;width:320px;height:180px">审批单列表</div>';
        document.body.appendChild(root);
        let n = 0;
        const timer = setInterval(() => {
          n += 1;
          root.querySelector('.ant-modal').style.opacity = String(0.2 + n * 0.2);
          if (n >= 5) clearInterval(timer);
        }, 20);
      }, 60);
    });
  </script>
</body></html>
"""

SPINNER_PAGE = """
<!doctype html><html><body>
  <div class="ant-spin-spinning" style="position:fixed;left:0;top:0">loading</div>
  <script>
    let n = 0;
    // 持续 loading + 持续 DOM 变更:收敛必须被 loading 拦住,不许提前收口
    setInterval(() => { n += 1; document.querySelector('.ant-spin-spinning').textContent = 'loading ' + n; }, 15);
  </script>
</body></html>
"""

_SYSTEM_BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


async def _launch_page(playwright):
    """返回 (browser, page);全部候选都失败时抛 RuntimeError。"""
    attempts: list[tuple[str, dict]] = [("bundled-chromium", {})]
    explicit = os.getenv("QA_AUTOMATION_TEST_BROWSER", "").strip()
    if explicit:
        attempts.insert(0, (explicit, {"executable_path": explicit}))
    attempts.extend(
        (path, {"executable_path": path})
        for path in _SYSTEM_BROWSERS
        if os.path.exists(path)
    )
    errors: list[str] = []
    for label, kwargs in attempts:
        try:
            browser = await playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-gpu"], **kwargs
            )
        except Exception as exc:  # noqa: BLE001 - 逐个候选降级,最后统一汇报
            errors.append(f"{label}: {exc}".splitlines()[0][:180])
            continue
        return browser, await browser.new_page(viewport={"width": 1280, "height": 800})
    raise RuntimeError("; ".join(errors) or "no chromium candidate")


class AdaptiveSettleBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Playwright unavailable: {exc}") from exc
        self._pw = await async_playwright().start()
        try:
            self.browser, self.page = await _launch_page(self._pw)
        except Exception as exc:  # noqa: BLE001
            await self._pw.stop()
            raise unittest.SkipTest(f"Chromium browser unavailable: {exc}") from exc

    async def asyncTearDown(self) -> None:
        try:
            await self.browser.close()
        finally:
            await self._pw.stop()

    async def test_converged_window_keeps_the_overlay_event(self) -> None:
        await self.page.set_content(MODAL_PAGE)
        await _install_overlay_observers(self.page, reset=True)
        await self.page.click("#open")

        started = time.monotonic()
        info = await _await_overlay_settle(self.page, 600)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        drained = await _drain_overlay_observers(self.page, stop=True)

        self.assertEqual(info["settle_mode"], "converged")
        self.assertTrue(info["settle_observed_mutations"])
        self.assertLess(elapsed_ms, 600)
        # 收口提前了,但事件必须一个不少:这正是"提前返回"最危险的失效模式
        kinds = [item.get("kind") for item in drained["events"]]
        self.assertIn("dialog", kinds)
        self.assertIn("dialog", [item.get("kind") for item in drained["current"]])

    async def test_quiet_page_still_consumes_the_whole_window(self) -> None:
        await self.page.set_content("<div id='static'>静默页面</div>")
        await _install_overlay_observers(self.page, reset=True)

        started = time.monotonic()
        info = await _await_overlay_settle(self.page, 300)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # 无变更时不得提前返回:晚到的浮层必须在观察窗口内被等到
        self.assertEqual(info["settle_mode"], "settled")
        self.assertGreaterEqual(elapsed_ms, 290)
        self.assertLess(elapsed_ms, 300 + 150)

    async def test_loading_spinner_blocks_early_exit(self) -> None:
        await self.page.set_content(SPINNER_PAGE)
        await _install_overlay_observers(self.page, reset=True)

        started = time.monotonic()
        info = await _await_overlay_settle(self.page, 400)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        self.assertEqual(info["settle_mode"], "settled")
        self.assertGreaterEqual(elapsed_ms, 390)
        self.assertLess(elapsed_ms, 400 + 150)


if __name__ == "__main__":
    unittest.main()
