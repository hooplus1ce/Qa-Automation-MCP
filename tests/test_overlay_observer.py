"""Integration coverage for the cross-frame Ant Design overlay observer."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

import qa_automation as automation

FRAME_DOCUMENT = """
<!doctype html>
<html><body>
  <button id="modal">Open modal</button>
  <button id="message">Show message</button>
  <script>
    const add = (className, text, role) => {
      const node = document.createElement("div");
      node.className = className;
      node.textContent = text;
      node.style.cssText = "position:fixed;display:block;left:10px;top:10px;width:200px;height:40px";
      if (role) node.setAttribute("role", role);
      document.body.append(node);
      return node;
    };

    document.querySelector("#modal").addEventListener("click", () => {
      add("ant-modal-root", "Iframe modal content", "dialog");
    });
    document.querySelector("#message").addEventListener("click", () => {
      const node = add("ant-message-notice", "Saved from iframe", "status");
      setTimeout(() => node.remove(), 10);
    });
  </script>
</body></html>
"""


class OverlayObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        try:
            await automation.start_browser(headless=True)
        except Exception as exc:
            raise unittest.SkipTest(f"Playwright browser unavailable: {exc}") from exc
        self.page = await automation.current_page()
        await self.page.set_content(
            "<button id='top-message'>Show top message</button>"
            "<script>"
            "document.querySelector('#top-message').addEventListener('click', () => {"
            " const node = document.createElement('div');"
            " node.className = 'ant-message-notice'; node.setAttribute('role', 'status');"
            " node.textContent = 'Saved at top level';"
            " node.style.cssText = 'position:fixed;display:block;left:10px;top:10px;width:200px;height:40px';"
            " document.body.append(node); setTimeout(() => node.remove(), 10);"
            "});"
            "</script>"
            "<iframe id='application' srcdoc=\""
            + FRAME_DOCUMENT.replace("&", "&amp;").replace('"', "&quot;")
            + "\"></iframe>"
        )
        await self.page.locator("iframe").wait_for()
        await self.page.frames[1].get_by_role("button", name="Open modal").wait_for()

    async def _mount_fake_vtable(self) -> None:
        await self.page.set_content(
            "<div class='vtable' style='width:300px;height:120px'>"
            "<canvas width='300' height='120' style='width:300px;height:120px'></canvas>"
            "</div>"
        )
        await self.page.evaluate(
            """() => {
              const host = document.querySelector('.vtable');
              const canvas = host.querySelector('canvas');
              const data = [
                ['Name', 'SKU'],
                ['Alpha', 'A-001'],
                ['Beta', 'B-002'],
                ['Gamma', 'C-003'],
              ];
              const fields = ['name', 'sku'];
              host.__vtable__ = {
                canvas,
                rowCount: 4,
                colCount: 2,
                headerRowCount: 1,
                columnHeaderLevelCount: 1,
                frozenRowCount: 0,
                frozenColCount: 0,
                options: { editCellTrigger: 'click' },
                getCellRect() { return {}; },
                getCellRelativeRect(col, row) {
                  return { left: col * 150, top: row * 30, right: (col + 1) * 150, bottom: (row + 1) * 30 };
                },
                getCellValue(col, row) { return data[row][col]; },
                getCellType(col, row) { return col === 1 && row > 0 ? 'link' : 'text'; },
                getBodyField(col) { return fields[col]; },
                getHeaderField(col) { return fields[col]; },
                isHeader(col, row) { return row === 0; },
                getBodyColumnDefine(col) {
                  return col === 0
                    ? { title: 'Name', sort: true }
                    : { title: 'SKU', filter: true };
                },
                getCellIcons(col) { return [{ funcType: col === 0 ? 'sort' : 'filter' }]; },
                getEditor(col, row) {
                  return col === 0 && row > 0 ? { constructor: { name: 'InputEditor' } } : null;
                },
                getCustomLayout() { return null; },
                getCustomRender() { return null; },
                getRecordIndexByCell(col, row) { return row - 1; },
                getCellAddrByFieldRecord(field, recordIndex) {
                  return { col: fields.indexOf(field), row: Number(recordIndex) + 1 };
                },
                getTableIndexByField(field) { return fields.indexOf(field); },
                getTableIndexByRecordIndex(recordIndex) { return Number(recordIndex) + 1; },
                getCellHeaderPaths(col) { return { colHeaderPaths: [{ field: fields[col] }] }; },
                getSelectedCellRanges() { return []; },
                scrollToCell() {},
                scenegraph: {
                  getCell(col, row) {
                    if (row !== 0) return null;
                    const left = col * 150;
                    return {
                      name: 'cell',
                      children: [
                        {
                          name: 'text', type: 'text', attribute: {text: data[row][col]},
                          globalAABBBounds: {x1: left + 8, y1: 6, x2: left + 90, y2: 24},
                        },
                        {
                          name: col === 0 ? 'sort-icon' : 'filter-icon', type: 'image',
                          globalAABBBounds: {x1: left + 124, y1: 7, x2: left + 140, y2: 23},
                        },
                      ],
                    };
                  },
                },
              };
              canvas.addEventListener('click', event => {
                window.__vtableClick = { trusted: event.isTrusted, x: event.clientX, y: event.clientY };
              });
            }"""
        )

    async def asyncTearDown(self) -> None:
        await automation.close_browser()

    async def test_click_collects_modal_from_iframe_portal(self) -> None:
        result = await automation.click_dom_and_observe(
            "button",
            name="Open modal",
            frame="application",
            settle_ms=0,
        )

        self.assertEqual(result["status"], "clicked")
        modal = next(item for item in result["overlays"] if item["text"] == "Iframe modal content")
        self.assertEqual(modal["kind"], "dialog")
        self.assertEqual(modal["frame_name"], "application")
        self.assertTrue(modal["visible"])

    async def test_short_lived_message_is_retained_as_event(self) -> None:
        result = await automation.click_dom_and_observe(
            "button",
            name="Show message",
            frame="application",
            settle_ms=80,
        )

        self.assertEqual(result["status"], "clicked")
        message = next(item for item in result["ui_events"] if item["text"] == "Saved from iframe")
        self.assertEqual(message["kind"], "notification")
        self.assertEqual(message["frame_name"], "application")
        self.assertFalse(any(item["text"] == "Saved from iframe" for item in result["visible_overlays"]))

    async def test_top_level_portal_is_collected(self) -> None:
        result = await automation.click_dom_and_observe(
            "button",
            name="Show top message",
            settle_ms=80,
        )

        self.assertEqual(result["status"], "clicked")
        message = next(item for item in result["ui_events"] if item["text"] == "Saved at top level")
        self.assertEqual(message["kind"], "notification")
        self.assertEqual(message["frame_id"], "frame-0:unnamed")

    async def test_dynamic_iframe_portal_is_collected(self) -> None:
        dynamic_document = (
            "<div class='ant-modal-root' role='dialog' "
            "style='display:block;position:fixed;left:1px;top:1px;width:100px;height:30px'>"
            "Dynamic modal</div>"
        )
        await self.page.set_content(
            "<button id='spawn'>Open module</button>"
            "<script>document.querySelector('#spawn').onclick = () => {"
            " const frame = document.createElement('iframe'); frame.name = 'dynamic-module';"
            f" frame.srcdoc = {dynamic_document!r}; document.body.append(frame);"
            "};</script>"
        )

        result = await automation.click_dom_and_observe("button", name="Open module", settle_ms=80)

        self.assertEqual(result["status"], "clicked")
        modal = next(item for item in result["overlays"] if item["text"] == "Dynamic modal")
        self.assertEqual(modal["frame_name"], "dynamic-module")
        self.assertEqual(modal["kind"], "dialog")

    async def test_dynamic_iframe_short_message_is_retained(self) -> None:
        dynamic_document = (
            "<body><script>"
            "const node = document.createElement('div');"
            "node.className = 'ant-message-notice'; node.setAttribute('role', 'status');"
            "node.textContent = 'Dynamic short message';"
            "node.style.cssText = 'display:block;position:fixed;left:1px;top:1px;width:100px;height:30px';"
            "document.body.append(node); setTimeout(() => node.remove(), 5);"
            "</script></body>"
        )
        srcdoc_literal = json.dumps(dynamic_document).replace("</", "<\\/")
        await self.page.set_content(
            "<button id='spawn'>Open short module</button>"
            "<script>document.querySelector('#spawn').onclick = () => {"
            " const frame = document.createElement('iframe'); frame.name = 'short-module';"
            f" frame.srcdoc = {srcdoc_literal}; document.body.append(frame);"
            "};</script>"
        )

        result = await automation.click_dom_and_observe(
            "button", name="Open short module", settle_ms=100
        )

        self.assertEqual(result["status"], "clicked")
        message = next(
            item for item in result["ui_events"] if item["text"] == "Dynamic short message"
        )
        self.assertEqual(message["kind"], "notification")
        self.assertEqual(message["frame_name"], "short-module")

    async def test_character_data_update_is_retained(self) -> None:
        await self.page.set_content(
            "<div id='notice' class='ant-message-notice' role='status' "
            "style='display:block;position:fixed;left:1px;top:1px;width:100px;height:30px'>initial</div>"
            "<button id='update' style='margin-top:60px'>Update message</button>"
            "<script>document.querySelector('#update').onclick = () => {"
            " const node = document.querySelector('#notice'); node.firstChild.data = 'updated';"
            " setTimeout(() => node.remove(), 10); };"
            "</script>"
        )

        result = await automation.click_dom_and_observe("button", name="Update message", settle_ms=80)

        self.assertEqual(result["status"], "clicked")
        updated = next(item for item in result["ui_events"] if item["text"] == "updated")
        self.assertIn(updated["event"], {"changed", "removed"})

    async def test_synchronous_update_and_remove_is_retained(self) -> None:
        await self.page.set_content(
            "<button id='update'>Commit message</button>"
            "<script>document.querySelector('#update').onclick = () => {"
            " const node = document.createElement('div');"
            " node.className = 'ant-message-notice'; node.setAttribute('role', 'status');"
            " node.textContent = 'before'; document.body.append(node);"
            " node.firstChild.data = 'committed'; node.remove(); };"
            "</script>"
        )

        result = await automation.click_dom_and_observe(
            "button", name="Commit message", settle_ms=40
        )

        self.assertEqual(result["status"], "clicked")
        message = next(
            item for item in result["ui_events"] if item["text"] == "committed"
        )
        self.assertIn(message["event"], {"added", "removed"})
        self.assertFalse(
            any(item["text"] == "committed" for item in result["visible_overlays"])
        )

    async def test_frame_id_survives_frame_reordering(self) -> None:
        await self.page.set_content(
            "<iframe name='first'></iframe><iframe name='second'></iframe><iframe name='third'></iframe>"
        )
        await self.page.wait_for_timeout(20)
        second = next(frame for frame in self.page.frames if frame.name == "second")
        before = automation._frame_id(self.page, second)
        await self.page.locator("iframe").first.evaluate("element => element.remove()")
        await self.page.wait_for_timeout(20)
        after = automation._frame_id(self.page, second)

        self.assertEqual(before, after)

    async def test_observer_is_stopped_when_click_raises(self) -> None:
        original = automation._click_dom_impl

        async def explode(*args, **kwargs):
            raise RuntimeError("synthetic click failure")

        automation._click_dom_impl = explode
        try:
            result = await automation.click_dom_and_observe("button", name="missing", settle_ms=0)
        finally:
            automation._click_dom_impl = original

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["observer_cleanup_failed"])
        self.assertFalse(
            await self.page.evaluate(
                "key => Boolean(window[key])", automation.OVERLAY_OBSERVER_KEY
            )
        )

    async def test_stream_baseline_advances_when_not_stopped(self) -> None:
        await self.page.set_content(
            "<div id='notice' class='ant-message-notice' role='status' "
            "style='display:block;position:fixed;left:1px;top:1px;width:100px;height:30px'>one</div>"
        )
        first = await automation.observe_overlays(settle_ms=0, stop=False)
        self.assertEqual(first["status"], "ok")
        await self.page.locator("#notice").evaluate("element => element.firstChild.data = 'two'")
        second = await automation.observe_overlays(settle_ms=0, stop=False)
        third = await automation.observe_overlays(settle_ms=0, stop=True)

        self.assertTrue(any(item["text"] == "two" for item in second["overlays"]))
        self.assertFalse(any(item["text"] == "two" for item in third["overlays"]))

    async def test_active_application_iframe_is_preferred(self) -> None:
        await self.page.set_content(
            "<div class='ant-tabs-tabpane' role='tabpanel' aria-hidden='true'>"
            "<iframe name='hidden-module' srcdoc=\"<button>Hidden</button>\"></iframe>"
            "</div>"
            "<div class='ant-tabs-tabpane' role='tabpanel' aria-hidden='false'>"
            "<iframe name='active-module' srcdoc=\"<button>Active</button>\"></iframe>"
            "</div>"
        )
        await self.page.wait_for_timeout(30)
        active = await automation.active_application_frame(self.page)
        self.assertIsNotNone(active)
        self.assertEqual(active.name, "active-module")
        resolved = await automation.resolve_frame(self.page, "active")
        self.assertEqual(resolved.name, "active-module")

    async def test_custom_vtable_popup_focus_and_page_box(self) -> None:
        await self.page.set_content(
            "<button id='open'>Open filter</button>"
            "<script>document.querySelector('#open').onclick = () => {"
            " const node = document.createElement('div');"
            " node.className = 'vtable__popup'; node.setAttribute('role', 'menu');"
            " node.textContent = 'Filter options';"
            " node.style.cssText = 'display:block;position:fixed;left:12px;top:18px;width:140px;height:40px';"
            " document.body.append(node);"
            "};</script>"
        )
        result = await automation.click_dom_and_observe(
            "button", name="Open filter", settle_ms=20
        )
        self.assertEqual(result["status"], "clicked")
        popup = next(item for item in result["overlays"] if item["text"] == "Filter options")
        self.assertEqual(popup["kind"], "dropdown")
        self.assertEqual(popup["scope"], "top_document")
        self.assertEqual(popup["page_box"]["x"], 12)
        self.assertEqual(result["context"]["focus_layer"]["kind"], "dropdown")

    async def test_unified_dom_interact_uses_active_scope(self) -> None:
        await self.page.set_content(
            "<button id='open'>Open dialog</button>"
            "<script>document.querySelector('#open').onclick = () => {"
            " const node = document.createElement('div');"
            " node.className = 'ant-modal-wrap'; node.setAttribute('role', 'dialog');"
            " node.textContent = 'Unified dialog';"
            " node.style.cssText = 'display:block;position:fixed;left:1px;top:1px;width:120px;height:30px';"
            " document.body.append(node);"
            "};</script>"
        )
        result = await automation.dom_interact(
            "click", css="#open", observe_after=True, settle_ms=20
        )
        self.assertEqual(result["status"], "acted")
        self.assertEqual(result["context"]["focus_layer"]["kind"], "dialog")
        self.assertTrue(any(item["text"] == "Unified dialog" for item in result["overlays"]))
        self.assertEqual(result["interaction"]["locator"]["resolved_by"], "css")
        self.assertEqual(result["interaction"]["confidence"], "high")
        self.assertEqual(
            set(result["interaction"]),
            {
                "target", "frame", "locator", "coordinate", "action",
                "before_state", "after_state", "evidence", "confidence",
            },
        )

    async def test_scope_analysis_keeps_only_focused_iframe_controls(self) -> None:
        frame_document = (
            "<button>Background action</button>"
            "<div class='ant-modal-wrap' role='dialog' "
            "style='display:block;position:fixed;left:5px;top:5px;width:260px;height:120px'>"
            "<label for='customer'>Customer</label><input id='customer'>"
            "<button>Confirm</button></div>"
        )
        await self.page.set_content(
            "<button>Top background</button>"
            "<div class='ant-tabs-tabpane' role='tabpanel' aria-hidden='false'>"
            "<iframe name='focused-module' srcdoc=\""
            + frame_document.replace("&", "&amp;").replace('"', "&quot;")
            + "\"></iframe></div>"
        )
        await self.page.locator("iframe").wait_for()
        await self.page.frames[1].get_by_role("button", name="Confirm").wait_for()

        result = await automation.analyze_scope(max_controls=10)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scope"]["mode"], "focus_layer")
        names = {control["name"] for control in result["controls"]}
        self.assertIn("Customer", names)
        self.assertIn("Confirm", names)
        self.assertNotIn("Background action", names)
        self.assertNotIn("Top background", names)
        self.assertTrue(all(control["frame"] == "active" for control in result["controls"]))
        self.assertTrue(
            all(
                "locator" not in control and "box" not in control
                for control in result["controls"]
            )
        )

    async def test_antd_select_uses_portal_in_active_iframe(self) -> None:
        frame_document = (
            "<div class='ant-select'><div id='warehouse' role='combobox' "
            "aria-label='Warehouse' tabindex='0'>Choose</div></div>"
            "<script>document.querySelector('#warehouse').onclick = () => {"
            " if (document.querySelector('.ant-select-dropdown')) return;"
            " const drop = document.createElement('div'); drop.className='ant-select-dropdown';"
            " drop.setAttribute('role','listbox');"
            " drop.style.cssText='display:block;position:fixed;left:1px;top:30px;width:160px;height:80px';"
            " for (const text of ['East warehouse','West warehouse']) {"
            "  const option=document.createElement('div'); option.className='ant-select-item-option';"
            "  option.setAttribute('role','option'); option.textContent=text;"
            "  option.onclick=()=>{window.__selected=text;document.querySelector('#warehouse').textContent=text;drop.remove();};"
            "  drop.append(option); } document.body.append(drop); };</script>"
        )
        await self.page.set_content(
            "<div class='ant-tabs-tabpane' role='tabpanel' aria-hidden='false'>"
            "<iframe name='select-module' srcdoc=\""
            + frame_document.replace("&", "&amp;").replace('"', "&quot;")
            + "\"></iframe></div>"
        )
        await self.page.locator("iframe").wait_for()
        frame = self.page.frames[1]
        await frame.get_by_role("combobox", name="Warehouse").wait_for()

        result = await automation.dom_interact(
            "select",
            role="combobox",
            name="Warehouse",
            value="East warehouse",
            observe_after=True,
            settle_ms=20,
        )

        self.assertEqual(result["status"], "acted")
        self.assertEqual(result["action_detail"]["component"], "antd-select")
        self.assertEqual(result["action_detail"]["match"]["match"], "exact-role")
        self.assertEqual(await frame.evaluate("window.__selected"), "East warehouse")

    async def test_vtable_analysis_reports_interaction_evidence_and_coordinates(self) -> None:
        await self._mount_fake_vtable()

        analysis = await automation.vtable_analysis(max_columns=2, sample_rows=2)
        resolved = await automation.resolve_vtable_cell("sku", 1)
        clicked = await automation.click_vtable_cell_by_field("sku", 1, verify=False)

        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(
            [column["field"] for column in analysis["analysis"]["columns"]],
            ["name", "sku"],
        )
        name_column, sku_column = analysis["analysis"]["columns"]
        self.assertEqual(name_column["header_icons"][0]["function"], "sort")
        self.assertIn("point", name_column["header_icons"][0]["geometry"])
        self.assertTrue(name_column["sample_cells"][0]["editor"]["click_opens_dom_input"])
        self.assertEqual(name_column["sample_cells"][0]["editor"]["expected_dom_tags"], ["input"])
        self.assertTrue(sku_column["sample_cells"][0]["interaction"]["clickable"])
        self.assertEqual(sku_column["sample_cells"][0]["interaction"]["kind"], "link")
        self.assertIn("point", sku_column["sample_cells"][0]["geometry"])
        self.assertEqual(resolved["address"], {"col": 1, "row": 2})
        self.assertEqual(resolved["resolved_by"], "getCellAddrByFieldRecord")
        self.assertEqual(clicked["status"], "clicked")
        self.assertEqual(clicked["target"]["field"], "sku")
        self.assertEqual(clicked["target"]["record_index"], 1)
        self.assertTrue((await self.page.evaluate("window.__vtableClick"))["trusted"])

    async def test_vtable_click_without_verification_does_not_capture_visual_evidence(self) -> None:
        await self._mount_fake_vtable()

        with (
            patch.object(automation, "_cell_visual_state", new_callable=AsyncMock) as visual,
            patch.object(automation, "_cell_screenshot", new_callable=AsyncMock) as screenshot,
        ):
            result = await automation.click_vtable_cell_by_field("sku", 1, verify=False)

        self.assertEqual(result["status"], "clicked")
        visual.assert_not_awaited()
        screenshot.assert_not_awaited()
        self.assertEqual(result["interaction"]["before_state"], {"selection": []})
        self.assertEqual(result["interaction"]["evidence"], [])

    async def test_vtable_analysis_coordinates_reuse_generic_viewport_click(self) -> None:
        await self._mount_fake_vtable()

        analysis = await automation.vtable_analysis(max_columns=2, sample_rows=0)
        sort_icon = analysis["analysis"]["columns"][0]["header_icons"][0]
        point = sort_icon["geometry"]["point"]
        clicked = await automation.dom_interact(
            "click",
            x=point["x"],
            y=point["y"],
            observe_after=False,
        )
        event = await self.page.evaluate("window.__vtableClick")

        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(sort_icon["function"], "sort")
        self.assertEqual(clicked["status"], "acted")
        self.assertTrue(event["trusted"])
        self.assertAlmostEqual(event["x"], point["x"], delta=1)
        self.assertAlmostEqual(event["y"], point["y"], delta=1)

    async def test_vtable_click_verifies_scenegraph_visual_change(self) -> None:
        await self._mount_fake_vtable()
        await self.page.evaluate(
            """() => {
              const table = document.querySelector('.vtable').__vtable__;
              window.__scenePaint = '#ffffff';
              table.scenegraph.getCell = (col, row) => row > 0 ? {
                type: 'group', attribute: {fill: window.__scenePaint}, children: []
              } : null;
              table.getSelectedCellRanges = () => [];
              table.canvas.addEventListener('click', () => { window.__scenePaint = '#d6e4ff'; });
            }"""
        )

        result = await automation.click_vtable_cell_by_field("sku", 1, verify=True)

        self.assertEqual(result["status"], "clicked")
        self.assertTrue(result["verification"]["scenegraph_changed"])
        self.assertEqual(result["verification"]["scenegraph"]["after_paints"], ["#d6e4ff"])
        self.assertEqual(result["interaction"]["target"]["field"], "sku")
        self.assertTrue(
            next(
                item for item in result["interaction"]["evidence"]
                if item["type"] == "scenegraph-changed"
            )["matched"]
        )

    async def test_vtable_click_uses_screenshot_when_scenegraph_is_static(self) -> None:
        await self._mount_fake_vtable()
        await self.page.evaluate(
            """() => {
              const table = document.querySelector('.vtable').__vtable__;
              const ctx = table.canvas.getContext('2d');
              ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, 300, 120);
              table.scenegraph.getCell = (col, row) => row > 0 ? {
                type: 'group', attribute: {fill: '#ffffff'}, children: []
              } : null;
              table.getSelectedCellRanges = () => [];
              table.canvas.addEventListener('click', () => {
                ctx.fillStyle = '#d6e4ff'; ctx.fillRect(150, 60, 150, 30);
              });
            }"""
        )

        result = await automation.click_vtable_cell_by_field("sku", 1, verify=True)

        self.assertEqual(result["status"], "clicked")
        self.assertFalse(result["verification"]["scenegraph_changed"])
        self.assertTrue(result["verification"]["screenshot"]["changed"])

    async def test_vtable_analysis_accepts_fresh_coordinate(self) -> None:
        await self._mount_fake_vtable()
        analysis = await automation.vtable_analysis(max_columns=2, sample_rows=1)
        point = analysis["analysis"]["columns"][0]["header_icons"][0]["geometry"]["point"]

        result = await automation.dom_interact(
            "click",
            x=point["x"],
            y=point["y"],
            analysis_id=analysis["analysis_id"],
            observe_after=False,
        )

        self.assertEqual(result["status"], "acted")
        self.assertNotIn("reason", result)
        click_event = await self.page.evaluate("window.__vtableClick")
        self.assertIsNotNone(click_event)
        self.assertTrue(click_event["trusted"])

    async def test_vtable_analysis_rejects_stale_coordinate(self) -> None:
        await self._mount_fake_vtable()
        analysis = await automation.vtable_analysis(max_columns=2, sample_rows=1)
        point = analysis["analysis"]["columns"][0]["header_icons"][0]["geometry"]["point"]
        await self.page.evaluate("window._vtable.scrollLeft = 25; window.__vtableClick = null")

        result = await automation.dom_interact(
            "click",
            x=point["x"],
            y=point["y"],
            analysis_id=analysis["analysis_id"],
            observe_after=False,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "stale-coordinate")
        self.assertIsNone(await self.page.evaluate("window.__vtableClick"))

    async def test_coordinate_click_verifies_focused_dom_editor(self) -> None:
        await self.page.set_content(
            "<button id='edit' style='position:absolute;left:40px;top:40px;width:80px;height:30px'>Edit</button>"
            "<script>document.querySelector('#edit').onclick = () => {"
            " const input = document.createElement('input'); input.name = 'cell-editor';"
            " document.body.append(input); input.focus(); };</script>"
        )
        box = await self.page.locator("#edit").bounding_box()

        result = await automation.dom_interact(
            "click",
            x=box["x"] + box["width"] / 2,
            y=box["y"] + box["height"] / 2,
            expect_input=True,
            observe_after=False,
        )

        self.assertEqual(result["status"], "acted")
        self.assertTrue(result["activation"]["verified"])
        self.assertEqual(result["activation"]["element"]["selector"], 'input[name="cell-editor"]')

    async def test_custom_layout_requires_actionable_scenegraph_evidence(self) -> None:
        await self._mount_fake_vtable()
        await self.page.evaluate(
            """() => {
              const table = document.querySelector('.vtable').__vtable__;
              const original = table.scenegraph.getCell.bind(table.scenegraph);
              table.getEditor = () => null;
              table.getCellType = () => 'text';
              table.getCustomLayout = (col, row) => col === 0 && row > 0 ? {} : null;
              table.scenegraph.getCell = (col, row) => row === 0 ? original(col, row) : ({
                name: 'cell', children: [{
                  name: 'custom-container', type: 'group', attribute: {pickable: false},
                  globalAABBBounds: {x1: 0, y1: 30, x2: 150, y2: 60},
                  children: [{type: 'text', attribute: {text: 'status', pickable: false},
                    globalAABBBounds: {x1: 20, y1: 35, x2: 80, y2: 55}}],
                }],
              });
            }"""
        )

        analysis = await automation.vtable_analysis(fields=["name"], sample_rows=1)
        cell = analysis["analysis"]["columns"][0]["sample_cells"][0]

        self.assertEqual(cell["interaction"]["confidence"], "candidate")
        self.assertFalse(cell["interaction"]["clickable"])
        self.assertNotIn("targets", cell)

    async def test_static_navigation_menu_is_not_an_overlay(self) -> None:
        await self.page.set_content(
            "<ul class='ant-menu ant-menu-inline' role='menu' "
            "style='display:block;width:180px;height:500px'><li role='menuitem'>Navigation</li></ul>"
        )

        result = await automation.scan_overlays(scope="all")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["overlays"], [])

    async def test_vtable_analysis_uses_iframe_content_origin(self) -> None:
        await self.page.set_content(
            "<iframe name='vtable-module' "
            "style='position:absolute;left:90px;top:70px;width:340px;height:180px;border:7px solid black'></iframe>"
        )
        await self.page.locator("iframe").wait_for()
        frame = self.page.frames[1]
        await frame.set_content(
            "<div class='vtable' style='width:300px;height:120px'>"
            "<canvas width='300' height='120' style='width:300px;height:120px'></canvas>"
            "</div>"
        )
        await frame.evaluate(
            """() => {
              const host = document.querySelector('.vtable');
              const canvas = host.querySelector('canvas');
              host.__vtable__ = {
                canvas, colCount: 1, rowCount: 2, headerRowCount: 1,
                columnHeaderLevelCount: 1,
                getCellRect() { return {}; },
                getCellValue() { return 'Quantity'; },
                getBodyField() { return 'quantity'; },
                getHeaderField() { return 'quantity'; },
                isHeader(col, row) { return row === 0; },
                scenegraph: {
                  getCell(col, row) {
                    if (row !== 0) return null;
                    return {name: 'cell', children: [{
                      name: 'filter-icon', type: 'image',
                      globalAABBBounds: {x1: 100, y1: 8, x2: 116, y2: 24},
                    }]};
                  },
                },
              };
              canvas.onclick = event => { window.__iframeVtableClick = {trusted: event.isTrusted}; };
            }"""
        )

        analysis = await automation.vtable_analysis(max_columns=1, sample_rows=0)
        icon = analysis["analysis"]["columns"][0]["header_icons"][0]
        canvas_box = await frame.locator("canvas").bounding_box()
        point = icon["geometry"]["point"]
        result = await automation.dom_interact(
            "click", x=point["x"], y=point["y"], observe_after=False
        )

        self.assertAlmostEqual(point["x"], canvas_box["x"] + 108, delta=1)
        self.assertAlmostEqual(point["y"], canvas_box["y"] + 16, delta=1)
        self.assertEqual(result["status"], "acted")
        self.assertTrue(await frame.evaluate("window.__iframeVtableClick.trusted"))

    async def test_explicit_page_selection_survives_followup_calls(self) -> None:
        second = await self.page.context.new_page()
        await second.set_content("<title>Second module</title><button>Second</button>")
        listed = await automation.list_pages()
        second_id = next(
            item["page_id"] for item in listed["pages"] if item["title"] == "Second module"
        )

        selected = await automation.select_page(second_id)
        context = await automation.page_context(max_results=2)

        self.assertEqual(selected["status"], "selected")
        self.assertEqual(context["page_id"], second_id)
        self.assertEqual((await automation.current_page()), second)


    async def test_modal_resolves_to_inner_dialog_geometry_and_compact_changes(self) -> None:
        await self.page.set_content(
            "<button id='open-modal'>Open</button>"
            "<script>document.querySelector('#open-modal').onclick = () => {"
            " const wrap = document.createElement('div');"
            " wrap.className = 'ant-modal-wrap legions-pro-modal';"
            " wrap.setAttribute('role', 'dialog');"
            " wrap.style.cssText = 'position:fixed;left:0;top:0;width:1000px;height:800px;display:block';"
            " const modal = document.createElement('div');"
            " modal.className = 'ant-modal';"
            " modal.setAttribute('role', 'document');"
            " modal.style.cssText = 'position:absolute;left:200px;top:100px;width:600px;height:400px;display:block';"
            " modal.textContent = 'Actual dialog content';"
            " wrap.append(modal);"
            " document.body.append(wrap);"
            "};</script>"
        )

        result = await automation.dom_interact("click", css="#open-modal", settle_ms=80)
        self.assertEqual(result["status"], "acted")
        # Should resolve to 1 dialog (not duplicated wrap + inner dialog)
        dialog_overlays = [item for item in result["overlays"] if item.get("kind") == "dialog"]
        self.assertEqual(len(dialog_overlays), 1)
        dialog = dialog_overlays[0]
        self.assertEqual(dialog["text"], "Actual dialog content")
        self.assertEqual(dialog["page_box"]["x"], 200)
        self.assertEqual(dialog["page_box"]["width"], 600)
        self.assertIn("changes", result)
        self.assertEqual(result["changes"][0]["kind"], "dialog")
        self.assertEqual(result["changes"][0]["page_box"]["x"], 200)
        # Target should not contain null fields
        self.assertNotIn("xpath", result["interaction"]["target"])

    async def test_transitional_animation_events_are_deduplicated(self) -> None:
        await self.page.set_content(
            "<button id='trigger'>Trigger</button>"
            "<script>document.querySelector('#trigger').onclick = () => {"
            " const node = document.createElement('div');"
            " node.className = 'ant-message-notice move-up-enter';"
            " node.textContent = 'Toast message';"
            " node.style.cssText = 'position:fixed;left:10px;top:10px;width:100px;height:30px;display:block';"
            " document.body.append(node);"
            " setTimeout(() => { node.className = 'ant-message-notice'; }, 10);"
            "};</script>"
        )

        result = await automation.dom_interact("click", css="#trigger", settle_ms=80)
        self.assertEqual(result["status"], "acted")
        messages = [item for item in result["ui_events"] if item.get("text") == "Toast message"]
        # Only 1 deduplicated event instead of intermediate move-up-enter ghost
        self.assertEqual(len(messages), 1)
        self.assertNotIn("move-up-enter", messages[0]["selector"])

    async def test_iframe_control_click_glides_to_exact_viewport_center_without_double_offset(self) -> None:
        await self.page.set_content(
            "<iframe name='offset-frame' style='position:absolute;left:200px;top:150px;width:400px;height:300px;border:none;' "
            "srcdoc=\"<button id='target-btn' style='position:absolute;left:50px;top:30px;width:100px;height:40px;'>Click Target</button>\"></iframe>"
        )
        await self.page.wait_for_timeout(50)
        coords = []

        async def record_move(page, target_x, target_y):
            coords.append((target_x, target_y))
        with patch("qa_automation.interaction._smooth_mouse_move_to", side_effect=record_move):
            result = await automation.dom_interact(
                "click",
                css="#target-btn",
                frame="offset-frame",
                observe_after=False,
            )

        self.assertEqual(result["status"], "acted")
        self.assertEqual(len(coords), 1)
        glide_x, glide_y = coords[0]
        # Bounding box is at (200 + 50 = 250, 150 + 30 = 180, width=100, height=40)
        # True viewport center is (250 + 50 = 300, 180 + 20 = 200)
        self.assertAlmostEqual(glide_x, 300.0, delta=2)
        self.assertAlmostEqual(glide_y, 200.0, delta=2)

    async def test_static_scan_reads_current_dom_without_installing_observer(self) -> None:
        await self.page.set_content(
            "<div class='ant-popover' style='width:100px;height:40px'>First</div>"
        )
        first = await automation.scan_overlays(scope="all")
        observer_alive = await self.page.evaluate(
            "key => Boolean(window[key] && window[key].observer)",
            automation.OVERLAY_OBSERVER_KEY,
        )
        await self.page.set_content(
            "<div role='status' style='width:100px;height:40px'>Second</div>"
        )
        second = await automation.scan_overlays(scope="all")

        self.assertEqual([item["text"] for item in first["overlays"]], ["First"])
        self.assertFalse(observer_alive)
        self.assertEqual([item["text"] for item in second["overlays"]], ["Second"])

    async def test_active_scope_excludes_unrelated_iframe_without_active_module(self) -> None:
        await self.page.set_content(
            "<button>Top</button>"
            "<iframe name='unrelated' "
            "srcdoc=\"<div role='dialog' style='width:100px;height:40px'>Other</div>\">"
            "</iframe>"
        )
        await self.page.locator("iframe").wait_for()
        await self.page.wait_for_timeout(20)

        result = await automation.scan_overlays(scope="active")

        self.assertEqual(result["overlays"], [])

    async def test_notification_is_reported_without_becoming_focus_layer(self) -> None:
        await self.page.set_content(
            "<button>Continue</button>"
            "<div role='status' style='width:100px;height:40px'>Ready</div>"
        )

        result = await automation.scan_overlays(scope="all")

        self.assertEqual(result["overlays"][0]["kind"], "notification")
        self.assertIsNone(result["context"]["focus_layer"])

    async def test_nested_dropdown_is_preserved_and_becomes_focus_layer(self) -> None:
        await self.page.set_content(
            "<div id='dialog' role='dialog' style='width:300px;height:200px'>"
            "<button>Choose</button>"
            "<div id='options' role='listbox' style='width:120px;height:80px'>"
            "<div role='option'>Alpha</div></div></div>"
        )

        result = await automation.scan_overlays(scope="all")
        by_kind = {item["kind"]: item for item in result["overlays"]}

        self.assertEqual(set(by_kind), {"dialog", "dropdown"})
        self.assertEqual(
            by_kind["dropdown"]["parent_overlay_id"], by_kind["dialog"]["overlay_id"]
        )
        self.assertEqual(result["context"]["focus_layer"]["overlay_id"], by_kind["dropdown"]["overlay_id"])

    async def test_topmost_popover_wins_over_dialog_by_stack(self) -> None:
        await self.page.set_content(
            "<div id='dialog' role='dialog' "
            "style='position:fixed;z-index:100;width:300px;height:200px'>Dialog</div>"
            "<div id='popover' class='ant-popover' "
            "style='position:fixed;z-index:200;left:50px;top:50px;width:120px;height:60px'>"
            "Popover</div>"
        )

        result = await automation.scan_overlays(scope="all")

        self.assertEqual(result["context"]["focus_layer"]["selector"], "#popover")

    async def test_equal_text_overlays_remain_distinct_with_unique_css(self) -> None:
        await self.page.set_content(
            "<div class='confirm' role='dialog' "
            "style='position:fixed;width:100px;height:50px'>Confirm</div>"
            "<div class='confirm' role='dialog' "
            "style='position:fixed;left:120px;width:100px;height:50px'>Confirm</div>"
        )

        result = await automation.scan_overlays(scope="all")
        dialogs = [item for item in result["overlays"] if item["kind"] == "dialog"]
        selector_counts = await self.page.evaluate(
            "selectors => selectors.map(selector => document.querySelectorAll(selector).length)",
            [item["selector"] for item in dialogs],
        )

        self.assertEqual(len(dialogs), 2)
        self.assertEqual(selector_counts, [1, 1])
        self.assertEqual(len({item["overlay_id"] for item in dialogs}), 2)

    async def test_scope_analysis_exposes_antd_dropdown_items(self) -> None:
        await self.page.set_content(
            "<div class='ant-select-dropdown' role='listbox' "
            "style='width:180px;height:80px'>"
            "<div class='ant-select-item-option'>East warehouse</div>"
            "<div class='ant-select-item-option ant-select-item-option-disabled'>"
            "West warehouse</div></div>"
        )

        result = await automation.analyze_scope(max_controls=10)
        by_name = {item["name"]: item for item in result["controls"]}

        self.assertEqual(result["scope"]["mode"], "focus_layer")
        self.assertFalse(by_name["East warehouse"]["disabled"])
        self.assertTrue(by_name["West warehouse"]["disabled"])

    async def test_offscreen_overlay_is_rendered_but_not_viewport_visible(self) -> None:
        await self.page.set_content(
            "<div role='dialog' style='position:fixed;left:-5000px;top:0;"
            "width:100px;height:50px'>Parked</div>"
        )

        result = await automation.scan_overlays(scope="all")

        self.assertEqual(result["overlays"], [])
        self.assertIsNone(result["context"]["focus_layer"])

    async def test_observer_reports_event_buffer_truncation(self) -> None:
        await self.page.set_content("<main>APS</main>")
        armed = await automation.observe_overlays(settle_ms=0, stop=False)
        self.assertEqual(armed["status"], "ok")
        await self.page.evaluate(
            """count => {
              for (let i = 0; i < count; i++) {
                const item = document.createElement('div');
                item.id = `notice-${i}`;
                item.className = 'ant-message-notice';
                item.setAttribute('role', 'status');
                item.textContent = `Notice ${i}`;
                item.style.cssText =
                  `position:fixed;left:${i % 10}px;top:${i % 10}px;width:80px;height:20px`;
                document.body.append(item);
              }
            }""",
            automation.OVERLAY_EVENT_LIMIT + 25,
        )

        result = await automation.observe_overlays(settle_ms=20, stop=True)

        self.assertTrue(result["events_truncated"])
        self.assertGreaterEqual(result["dropped_event_count"], 25)

    async def test_mouse_drag_dispatches_sequential_events_with_continuous_movement(self) -> None:
        await self.page.set_content(
            "<canvas id='drag-box' width='300' height='300' style='position:absolute;left:10px;top:10px;width:300px;height:300px;'></canvas>"
            "<script>"
            "window.__dragEvents = [];"
            "const el = document.getElementById('drag-box');"
            "['mousedown', 'mousemove', 'mouseup'].forEach(t => {"
            " el.addEventListener(t, e => {"
            "  window.__dragEvents.push({type: t, x: e.clientX, y: e.clientY, buttons: e.buttons});"
            " });"
            "});"
            "</script>"
        )
        await self.page.wait_for_timeout(30)

        result = await automation.mouse_drag(
            start_x=30.0,
            start_y=30.0,
            end_x=180.0,
            end_y=90.0,
            steps=12,
            hold_ms=20,
            settle_ms=20,
        )

        self.assertEqual(result["status"], "dragged")
        self.assertEqual(result["start"], {"x": 30.0, "y": 30.0})
        self.assertEqual(result["end"], {"x": 180.0, "y": 90.0})
        self.assertEqual(result["steps"], 12)

        events = await self.page.evaluate("window.__dragEvents")
        types = [e["type"] for e in events]
        self.assertIn("mousedown", types)
        self.assertIn("mouseup", types)
        self.assertIn("mousemove", types)
        # Verify that during drag, mousemove had buttons pressed
        drag_moves = [e for e in events if e["type"] == "mousemove" and e["buttons"] > 0]
        self.assertGreaterEqual(len(drag_moves), 8)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
