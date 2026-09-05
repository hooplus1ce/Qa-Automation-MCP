"""Workspace-bound artifact and local file contracts."""

from __future__ import annotations

import asyncio
import base64
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from qa_automation import browser
from qa_automation.components import vtable as vtable_component
from qa_automation.interaction import snapshot
from qa_automation.workspace import (
    artifact_file,
    data_dir,
    resolve_workspace_path,
    workspace_root,
)


class WorkspacePathTests(unittest.TestCase):
    def test_relative_paths_resolve_inside_selected_workspace(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
                "QA_AUTOMATION_DATA_DIR": ".qa-automation/data",
            },
        ):
            root = Path(directory).resolve()

            self.assertEqual(workspace_root(), root)
            self.assertEqual(data_dir(), root / ".qa-automation" / "data")
            self.assertEqual(
                artifact_file("outputs", "result.json", fallback="output.json"),
                root / ".qa-automation" / "outputs" / "result.json",
            )

    def test_project_root_env_precedes_cwd_and_workspace_root(self) -> None:
        with TemporaryDirectory() as mcp_dir, TemporaryDirectory() as project_dir:
            # Process cwd is mcp_dir, but QA_AUTOMATION_PROJECT_ROOT points to project_dir
            with chdir(mcp_dir), patch.dict(
                "os.environ",
                {
                    "QA_AUTOMATION_PROJECT_ROOT": project_dir,
                    "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                    "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
                    "QA_AUTOMATION_DATA_DIR": ".qa-automation/data",
                },
            ):
                expected_root = Path(project_dir).resolve()
                mcp_root = Path(mcp_dir).resolve()

                self.assertNotEqual(expected_root, mcp_root)
                self.assertEqual(workspace_root(), expected_root)
                self.assertEqual(data_dir(), expected_root / ".qa-automation" / "data")
                self.assertEqual(
                    artifact_file("screenshots", "test.png", fallback="screenshot.png"),
                    expected_root / ".qa-automation" / "screenshots" / "test.png",
                )

    def test_project_dir_alias_precedes_cwd(self) -> None:
        with TemporaryDirectory() as mcp_dir, TemporaryDirectory() as project_dir:
            with chdir(mcp_dir), patch.dict(
                "os.environ",
                {
                    "QA_AUTOMATION_PROJECT_ROOT": "",
                    "QA_AUTOMATION_PROJECT_DIR": project_dir,
                    "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                },
            ):
                expected_root = Path(project_dir).resolve()
                self.assertEqual(workspace_root(), expected_root)
    def test_workspace_root_legacy_alias_compatibility(self) -> None:
        with TemporaryDirectory() as mcp_dir, TemporaryDirectory() as project_dir:
            with chdir(mcp_dir), patch.dict(
                "os.environ",
                {
                    "QA_AUTOMATION_PROJECT_ROOT": "",
                    "QA_AUTOMATION_PROJECT_DIR": "",
                    "QA_AUTOMATION_WORKSPACE_ROOT": project_dir,
                },
            ):
                expected_root = Path(project_dir).resolve()
                self.assertEqual(workspace_root(), expected_root)


    def test_workspace_paths_reject_parent_escape(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ", {"QA_AUTOMATION_WORKSPACE_ROOT": "."}
        ):
            outside = Path(directory).resolve().parent / "outside.txt"

            with self.assertRaisesRegex(
                ValueError,
                "must stay inside MCP consumer workspace",
            ):
                resolve_workspace_path(outside)


class WorkspaceArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_screenshot_is_persisted_under_workspace(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
            },
        ):
            frame = SimpleNamespace(name="", url="about:blank")
            page = SimpleNamespace(main_frame=frame)
            page.screenshot = AsyncMock(return_value=b"synthetic-png")
            with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=page)):
                result = await snapshot._screenshot_element_impl(
                    x=0,
                    y=0,
                    width=20,
                    height=10,
                    filename="viewport.png",
                )

            output = Path(result["path"])
            self.assertEqual(result["status"], "ok")
            self.assertNotIn("image_base64", result)
            self.assertEqual(
                output,
                Path(directory).resolve()
                / ".qa-automation"
                / "screenshots"
                / "viewport.png",
            )
            self.assertEqual(output.read_bytes(), b"synthetic-png")

    async def test_ui_screenshot_base64_is_opt_in(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
            },
        ):
            frame = SimpleNamespace(name="", url="about:blank")
            page = SimpleNamespace(main_frame=frame)
            page.screenshot = AsyncMock(return_value=b"synthetic-png")
            with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=page)):
                result = await snapshot._screenshot_element_impl(
                    x=0,
                    y=0,
                    width=20,
                    height=10,
                    include_base64=True,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                base64.b64decode(result["image_base64"]), b"synthetic-png"
            )
            output = Path(result["path"])
            self.assertEqual(output.read_bytes(), b"synthetic-png")
    async def test_ui_screenshot_defaults_to_full_viewport_when_no_coords_or_locator(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
            },
        ):
            frame = SimpleNamespace(name="", url="about:blank")
            page = SimpleNamespace(main_frame=frame)
            page.screenshot = AsyncMock(return_value=b"viewport-full-png")
            with patch.object(snapshot, "_current_page_impl", AsyncMock(return_value=page)), \
                 patch.object(snapshot, "_page_viewport_size", AsyncMock(return_value={"width": 1920, "height": 1080})):
                result = await snapshot._screenshot_element_impl(
                    filename="full_viewport.png",
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["clip"], {"x": 0.0, "y": 0.0, "width": 1920, "height": 1080})
            page.screenshot.assert_awaited_once_with(
                clip={"x": 0.0, "y": 0.0, "width": 1920, "height": 1080},
                type="png",
            )

    async def test_browser_download_uses_sanitized_workspace_filename(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
            },
        ):
            download = SimpleNamespace(
                suggested_filename="../report?.csv",
                failure=AsyncMock(return_value=None),
                save_as=AsyncMock(),
            )
            browser._state.download_failures.clear()

            await browser._persist_download(download)

            target = Path(download.save_as.await_args.args[0])
            self.assertEqual(
                target.parent,
                Path(directory).resolve() / ".qa-automation" / "downloads",
            )
            self.assertEqual(target.name, "report_.csv")
            self.assertEqual(browser._state.download_failures, [])

    async def test_real_browser_download_is_persisted_under_workspace(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ",
            {
                "QA_AUTOMATION_WORKSPACE_ROOT": ".",
                "QA_AUTOMATION_ARTIFACT_ROOT": ".qa-automation",
            },
        ):
            try:
                await browser.start_browser(headless=True)
            except Exception as exc:
                raise unittest.SkipTest(
                    f"Playwright browser unavailable: {exc}"
                ) from exc
            try:
                page = await browser.current_page()
                await page.set_content(
                    '<a id="download" download="report.txt" '
                    'href="data:text/plain,workspace-download">Download</a>'
                )
                async with page.expect_download() as pending:
                    await page.locator("#download").click()
                await (await pending.value).path()
                if browser._state.download_tasks:
                    await asyncio.gather(*list(browser._state.download_tasks))

                files = list(
                    (
                        Path(directory).resolve()
                        / ".qa-automation"
                        / "downloads"
                    ).glob("report*.txt")
                )
                self.assertEqual(len(files), 1)
                self.assertEqual(
                    files[0].read_text(encoding="utf-8"),
                    "workspace-download",
                )
            finally:
                await browser.close_browser()

    async def test_session_state_is_saved_inside_workspace(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ", {"QA_AUTOMATION_WORKSPACE_ROOT": "."}
        ):
            context = SimpleNamespace(storage_state=AsyncMock())
            current = SimpleNamespace(context=context)
            with (
                patch.object(browser._state, "browser", SimpleNamespace()),
                patch.object(browser._state, "selected_context", None),
                patch.object(
                    browser,
                    "_current_page_impl",
                    AsyncMock(return_value=current),
                ),
            ):
                result = await browser._browser_session_impl(
                    "save",
                    storage_state_path=".qa-automation/sessions/admin.json",
                )

            expected = (
                Path(directory).resolve()
                / ".qa-automation"
                / "sessions"
                / "admin.json"
            )
            context.storage_state.assert_awaited_once_with(path=str(expected))
            self.assertEqual(result["path"], str(expected))

    async def test_vtable_file_drop_rejects_outside_workspace(self) -> None:
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            "os.environ", {"QA_AUTOMATION_WORKSPACE_ROOT": "."}
        ):
            outside = Path(directory).resolve().parent / "outside-upload.txt"
            outside.write_text("outside", encoding="utf-8")
            current_page = AsyncMock()
            try:
                with (
                    patch.object(
                        vtable_component,
                        "_current_page_impl",
                        current_page,
                    ),
                    self.assertRaisesRegex(
                        ValueError,
                        "must stay inside MCP consumer workspace",
                    ),
                ):
                    await vtable_component._drop_files_impl(
                        0,
                        1,
                        [str(outside)],
                    )
            finally:
                outside.unlink(missing_ok=True)

            current_page.assert_not_awaited()



if __name__ == "__main__":  # pragma: no cover
    unittest.main()
