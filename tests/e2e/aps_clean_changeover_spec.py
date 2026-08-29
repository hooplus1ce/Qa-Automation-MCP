"""Real APS contracts. Run only after selecting the named APS module.

APS_E2E=1 uv run python -m unittest \
  tests.e2e.aps_clean_changeover_spec.ApsCleanChangeoverE2E -v

APS_E2E_DETAIL=1 bash scripts/run_aps_e2e.sh
"""

from __future__ import annotations

import os
import re
import unittest

import vtable_playwright as vpw


APS_E2E = os.getenv("APS_E2E") == "1"
APS_E2E_DETAIL = os.getenv("APS_E2E_DETAIL") == "1"
APS_DETAIL_RUN = os.getenv("APS_DETAIL_RUN") == "1"


@unittest.skipUnless(APS_E2E, "set APS_E2E=1 to use the browser on CDP port 9222")
class ApsCleanChangeoverE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await vpw.connect_browser(os.getenv("APS_CDP_URL", "http://127.0.0.1:9222"))
        context = await vpw.page_context()
        frame_url = (context.get("active_iframe") or {}).get("frame_url", "")
        if "/cleanChangeover" not in frame_url:
            self.skipTest("switch APS to 产品工艺 > 清洗改机设置")

    async def asyncTearDown(self) -> None:
        await vpw.close_browser()

    async def test_page_controls_and_main_vtable(self) -> None:
        context = await vpw.page_context()
        controls = await vpw.analyze_scope(max_controls=30)
        analysis = await vpw.vtable_analysis(
            mode="full", fields=["_op"], sample_rows=1
        )

        self.assertEqual(context["title"], "APS管理平台")
        names = {
            re.sub(r"\s+", "", str(item["name"]))
            for item in controls["controls"]
        }
        self.assertIn("查询", names)
        self.assertIn("设置", names)
        self.assertTrue("新增" in names or any("新增" in name for name in names))
        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(analysis["analysis"]["columns"][0]["field"], "_op")

    async def test_view_opens_modal_with_nested_vtable(self) -> None:
        analysis = await vpw.vtable_analysis(
            mode="full", fields=["_op"], sample_rows=1
        )
        targets = analysis["analysis"]["columns"][0]["sample_cells"][0]["targets"]
        view = next(item for item in targets if item["name"] == "查看")
        point = view["geometry"]["point"]
        try:
            clicked = await vpw.dom_interact(
                "click",
                x=point["x"],
                y=point["y"],
                analysis_id=analysis["analysis_id"],
                observe_after=True,
                settle_ms=200,
            )
            nested = await vpw.vtable_analysis(mode="full", sample_rows=1)

            self.assertEqual(clicked["status"], "acted")
            observed = [
                *clicked.get("visible_overlays", []),
                *clicked.get("overlays", []),
                *clicked.get("ui_events", []),
            ]
            if not any("查看清洗改机规则" in item.get("text", "") for item in observed):
                observed.extend((await vpw.scan_overlays(scope="active")).get("overlays", []))
            self.assertTrue(
                any("查看清洗改机规则" in item.get("text", "") for item in observed)
            )
            self.assertEqual(nested["status"], "ok")
            self.assertEqual(nested["table"]["context"], "modal")
            fields = {item["field"] for item in nested["analysis"]["columns"]}
            self.assertIn("stepName", fields)
        finally:
            await vpw.dom_interact(
                "press", css="body", key="Escape", frame="active", settle_ms=250
            )

    async def test_repeated_vtable_click_has_visual_evidence(self) -> None:
        first = await vpw.click_cell(2, 1, verify=True)
        second = await vpw.click_cell(2, 1, verify=True)

        self.assertEqual(first["status"], "clicked")
        self.assertEqual(second["status"], "clicked")
        self.assertTrue(any(item["matched"] for item in second["interaction"]["evidence"]))


@unittest.skipUnless(
    APS_E2E and APS_E2E_DETAIL and APS_DETAIL_RUN,
    "set APS_E2E=1 APS_E2E_DETAIL=1 APS_DETAIL_RUN=1 for the material detail page",
)
class ApsMaterialSubstituteDetailE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await vpw.connect_browser(os.getenv("APS_CDP_URL", "http://127.0.0.1:9222"))
        context = await vpw.page_context()
        frame_url = (context.get("active_iframe") or {}).get("frame_url", "")
        if "/materialSubstitutePlanDetail" not in frame_url:
            self.skipTest("switch APS to the material substitute detail module")

    async def asyncTearDown(self) -> None:
        await vpw.close_browser()

    async def test_two_vtables_and_regular_form_controls(self) -> None:
        directory = await vpw.vtable_analysis()
        controls = await vpw.analyze_scope(max_controls=50)

        self.assertEqual(directory["status"], "needs_table_selection")
        self.assertGreaterEqual(len(directory["tables"]), 2)
        roles = {item["role"] for item in controls["controls"]}
        self.assertIn("textbox", roles)
        self.assertIn("combobox", roles)

    async def test_vtable_editors_are_reported(self) -> None:
        directory = await vpw.vtable_analysis()
        editor_cells = []
        for table in directory["tables"][:2]:
            analysis = await vpw.vtable_analysis(
                table_index=table["table_index"], mode="full", sample_rows=3
            )
            for column in analysis.get("analysis", {}).get("columns", []):
                editor_cells.extend(
                    item
                    for item in column.get("sample_cells", [])
                    if item.get("editor", {}).get("available")
                )
        self.assertTrue(editor_cells)

    async def test_vtable_cell_activates_dom_editor(self) -> None:
        directory = await vpw.vtable_analysis()
        candidate = None
        source = None
        for table in directory["tables"][:2]:
            analysis = await vpw.vtable_analysis(
                table_index=table["table_index"], mode="full", sample_rows=3
            )
            for column in analysis.get("analysis", {}).get("columns", []):
                for cell in column.get("sample_cells", []):
                    if cell.get("editor", {}).get("available") and cell.get("geometry"):
                        candidate, source = cell, analysis
                        break
                if candidate:
                    break
            if candidate:
                break
        self.assertIsNotNone(candidate)
        point = candidate["geometry"]["point"]
        try:
            result = await vpw.dom_interact(
                "click",
                x=point["x"],
                y=point["y"],
                analysis_id=source["analysis_id"],
                expect_input=True,
                observe_after=True,
                settle_ms=200,
            )
            self.assertEqual(result["status"], "acted")
            self.assertTrue(result["activation"]["verified"])
        finally:
            await vpw.dom_interact(
                "press", css="body", key="Escape", frame="active", settle_ms=100
            )

    @unittest.skipUnless(
        os.getenv("APS_E2E_VALIDATE_SAVE") == "1",
        "set APS_E2E_VALIDATE_SAVE=1 only on a blank add page",
    )
    async def test_blank_save_emits_validation_message(self) -> None:
        controls = await vpw.analyze_scope(max_controls=60)
        save = next(
            item
            for item in controls["controls"]
            if item["role"] == "button"
            and re.sub(r"\s+", "", str(item["name"])) == "保存"
        )
        result = await vpw.dom_interact(
            "click",
            css=save["css"],
            role=save["role"],
            name=save["name"],
            frame=save["frame"],
            observe_after=True,
            settle_ms=300,
        )
        messages = " ".join(item.get("text", "") for item in result["ui_events"])
        self.assertIn("请完善表头必填信息", messages)
