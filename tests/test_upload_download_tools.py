"""Upload (file chooser / set_input_files) and download wait tool contracts.

不依赖真实浏览器：文件校验走 workspace 规则，浏览器交互用假 page/frame mock。
"""

from __future__ import annotations

import time
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from qa_automation.browser import downloads
from qa_automation.browser.state import _state
from qa_automation.interaction import _upload_files_impl


async def _run(coro):
    return await coro


class _FakeLocator:
    def __init__(self, count: int = 1, text: str = "") -> None:
        self._count = count
        self._text = text
        self.first = self
        self.files: list[str] | None = None
        self.click_calls = 0

    def nth(self, index: int) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return True

    async def inner_text(self) -> str:
        return self._text

    async def click(self, timeout: float | None = None) -> None:
        self.click_calls += 1

    async def set_files(self, files, timeout: float | None = None) -> None:
        self.files = list(files)


class _FakeChooser:
    def __init__(self, *, multiple: bool = True) -> None:
        self._multiple = multiple
        self.files: list[str] | None = None

    def is_multiple(self) -> bool:
        return self._multiple

    async def set_files(self, files, timeout: float | None = None) -> None:
        self.files = list(files)

    def element(self):  # impl 对 element 的探测失败会静默降级
        return None


class _FakeChooserContext:
    def __init__(self, chooser: _FakeChooser) -> None:
        self.chooser = chooser
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    @property
    def value(self):
        async def _resolve():
            return self.chooser

        return _resolve()


class _FakeFrame:
    name = "main"

    def __init__(self) -> None:
        self.file_inputs = _FakeLocator()
        self.triggers: dict[str, _FakeLocator] = {}

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == "input[type=file]"
        return self.file_inputs

    def get_by_text(self, text: str, *, exact: bool = True) -> _FakeLocator:
        return self.triggers.setdefault(text, _FakeLocator(text=text))


class _FakePage:
    def __init__(self, frame: _FakeFrame) -> None:
        self.main_frame = frame
        self.chooser_context: _FakeChooserContext | None = None

    def expect_file_chooser(self, timeout: float | None = None):
        assert self.chooser_context is not None, "expect_file_chooser called without setup"
        return self.chooser_context


def _workspace_env() -> dict[str, str]:
    return {
        "QA_AUTOMATION_WORKSPACE_ROOT": ".",
        "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
    }


class UploadFilesValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_empty_file_list(self) -> None:
        with self.assertRaises(ValueError):
            await _upload_files_impl([])

    async def test_rejects_paths_outside_workspace(self) -> None:
        with TemporaryDirectory() as workspace, TemporaryDirectory() as outside:
            with chdir(workspace), patch.dict("os.environ", _workspace_env()):
                with self.assertRaises(ValueError):
                    await _upload_files_impl([str(Path(outside) / "evil.txt")])

    async def test_rejects_missing_file(self) -> None:
        with TemporaryDirectory() as workspace, chdir(workspace), patch.dict(
            "os.environ", _workspace_env()
        ):
            with self.assertRaises(ValueError):
                await _upload_files_impl(["missing.txt"])


class UploadFilesInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _workspace_with_file(self) -> str:
        # 返回已 chdir 上下文内的相对路径文件名
        target = Path("uploads") / "sample.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hello", encoding="utf-8")
        return str(target).replace("\\", "/")

    async def test_file_chooser_mode_uploads_via_trigger(self) -> None:
        with TemporaryDirectory() as workspace, chdir(workspace), patch.dict(
            "os.environ", _workspace_env()
        ):
            relative = await self._workspace_with_file()
            frame = _FakeFrame()
            page = _FakePage(frame)
            chooser = _FakeChooser(multiple=True)
            page.chooser_context = _FakeChooserContext(chooser)

            with (
                patch("qa_automation.interaction._current_page_impl", AsyncMock(return_value=page)),
                patch("qa_automation.interaction.active_application_frame", AsyncMock(return_value=None)),
                patch(
                    "qa_automation.interaction.locator.active_application_frame",
                    AsyncMock(return_value=None),
                ),
            ):
                result = await _upload_files_impl(
                    [relative], text="Upload", timeout_ms=3000
                )

            self.assertEqual(result["status"], "uploaded")
            self.assertEqual(result["mode"], "file-chooser")
            self.assertEqual(result["trigger_source"], "text")
            self.assertEqual(result["files"], [str((Path(workspace) / relative).resolve())])
            self.assertTrue(chooser.files and chooser.files[0].endswith("sample.txt"))
            trigger = frame.triggers["Upload"]
            self.assertEqual(trigger.click_calls, 1)

    async def test_file_chooser_mode_rejects_multi_file_single_chooser(self) -> None:
        with TemporaryDirectory() as workspace, chdir(workspace), patch.dict(
            "os.environ", _workspace_env()
        ):
            first = await self._workspace_with_file()
            second = Path("uploads") / "second.txt"
            second.write_text("world", encoding="utf-8")
            frame = _FakeFrame()
            page = _FakePage(frame)
            page.chooser_context = _FakeChooserContext(_FakeChooser(multiple=False))

            with (
                patch("qa_automation.interaction._current_page_impl", AsyncMock(return_value=page)),
                patch("qa_automation.interaction.active_application_frame", AsyncMock(return_value=None)),
                patch(
                    "qa_automation.interaction.locator.active_application_frame",
                    AsyncMock(return_value=None),
                ),
            ):
                with self.assertRaises(ValueError):
                    await _upload_files_impl([first, str(second)], text="Upload")

    async def test_set_input_files_mode_targets_hidden_input(self) -> None:
        with TemporaryDirectory() as workspace, chdir(workspace), patch.dict(
            "os.environ", _workspace_env()
        ):
            relative = await self._workspace_with_file()
            frame = _FakeFrame()
            frame.file_inputs = _FakeLocator()
            page = _FakePage(frame)

            with (
                patch("qa_automation.interaction._current_page_impl", AsyncMock(return_value=page)),
                patch("qa_automation.interaction.active_application_frame", AsyncMock(return_value=None)),
            ):
                result = await _upload_files_impl([relative], timeout_ms=3000)

            self.assertEqual(result["status"], "uploaded")
            self.assertEqual(result["mode"], "set-input-files")
            self.assertEqual(result["frame"], "frame-0:main")
            self.assertTrue(frame.file_inputs.files and frame.file_inputs.files[0].endswith("sample.txt"))

    async def test_set_input_files_mode_fails_without_input(self) -> None:
        with TemporaryDirectory() as workspace, chdir(workspace), patch.dict(
            "os.environ", _workspace_env()
        ):
            relative = await self._workspace_with_file()
            frame = _FakeFrame()
            frame.file_inputs = _FakeLocator(count=0)
            page = _FakePage(frame)

            with (
                patch("qa_automation.interaction._current_page_impl", AsyncMock(return_value=page)),
                patch("qa_automation.interaction.active_application_frame", AsyncMock(return_value=None)),
            ):
                with self.assertRaises(ValueError):
                    await _upload_files_impl([relative], timeout_ms=1000)


class WaitDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / "downloads"
        self.directory.mkdir()
        patcher = patch.object(downloads, "artifact_dir", lambda category: self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name: str, content: bytes = b"data") -> Path:
        target = self.directory / name
        target.write_bytes(content)
        return target

    async def test_returns_completed_file(self) -> None:
        target = self._write("export.xlsx")

        result = await downloads.wait_download(
            timeout_ms=5_000, include_recent_seconds=60
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(Path(result["files"][0]["path"]), target)
        self.assertEqual(result["files"][0]["size"], len(b"data"))
        self.assertEqual(result["download_failures"], [])

    async def test_filename_filter_is_case_insensitive(self) -> None:
        self._write("Quarterly_REPORT.xlsx")
        self._write("other.txt")

        result = await downloads.wait_download(
            timeout_ms=5_000, filename_contains="report", include_recent_seconds=60
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["name"] for item in result["files"]], ["Quarterly_REPORT.xlsx"])

    async def test_skips_partial_downloads(self) -> None:
        self._write("report.crdownload")

        result = await downloads.wait_download(timeout_ms=600, include_recent_seconds=60)

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["recent_files"], [])

    async def test_timeout_reports_directory_snapshot(self) -> None:
        self._write("old.txt")

        result = await downloads.wait_download(timeout_ms=600, include_recent_seconds=0)

        self.assertEqual(result["status"], "timeout")
        # include_recent_seconds=0：调用之前已写完的文件不算候选，只等新出现的
        self.assertEqual(result["recent_files"], [])

    async def test_surfaces_and_drains_download_failures(self) -> None:
        self._write("export.xlsx")
        _state.download_failures.append("disk full")

        result = await downloads.wait_download(
            timeout_ms=5_000, include_recent_seconds=60
        )

        self.assertEqual(result["download_failures"], ["disk full"])
        self.assertEqual(list(_state.download_failures), [])

    async def test_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            await downloads.wait_download(timeout_ms=0)
        with self.assertRaises(ValueError):
            await downloads.wait_download(include_recent_seconds=-1)


class ScanCandidatesTests(unittest.TestCase):
    def test_cutoff_and_filter_and_partial_suffixes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fresh = root / "fresh.bin"
            fresh.write_bytes(b"a")
            old = root / "old.bin"
            old.write_bytes(b"b")
            partial = root / "partial.crdownload"
            partial.write_bytes(b"c")
            matching = root / "MATCH_lower.xlsx"
            matching.write_bytes(b"d")
            past = time.time_ns() + 1_000_000_000  # 1s 之后的文件全部被 cutoff 排除

            candidates = downloads._scan_download_candidates(
                root, cutoff_ns=past, filename_contains=None
            )
            self.assertEqual(candidates, {})

            candidates = downloads._scan_download_candidates(
                root, cutoff_ns=0, filename_contains="match_lower"
            )
            self.assertEqual(list(candidates), ["MATCH_lower.xlsx"])
            self.assertEqual(candidates["MATCH_lower.xlsx"]["size"], 1)


if __name__ == "__main__":
    unittest.main()
