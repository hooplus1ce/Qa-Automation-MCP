"""AntV X6 flowchart and topology canvas automation adapter for Playwright."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Frame, Page

from ...browser import current_page
from ...profiles import active_profile
from .scripts import BIND_X6, EXTRACT_GRAPH, FIT_VIEW


@dataclass
class X6Session:
    """X6 canvas session referencing Playwright page, frame, and offset."""

    page: Page
    frame: Frame
    offset_x: float = 0.0
    offset_y: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_viewport(self, x: float, y: float) -> tuple[float, float]:
        """Convert iframe local CSS coordinates to top-level page viewport coordinates."""
        return (round(x + self.offset_x, 1), round(y + self.offset_y, 1))


async def bind_x6(
    page: Page | None = None,
    frame: str | None = None,
    auto_fit: bool = True,
) -> X6Session:
    """Locate the X6 canvas iframe, bind the graph instance, and establish session."""
    if page is None:
        page = await current_page()

    target_frame: Frame | None = None

    # 1. 尝试使用活跃 iframe
    if not frame or frame == "active":
        profile = active_profile()
        try:
            iframe_ele = await page.query_selector(profile.active_iframe_selector)
            if iframe_ele:
                content_frame = await iframe_ele.content_frame()
                if content_frame:
                    target_frame = content_frame
        except Exception:
            pass

    # 2. 尝试全量 frame 检索包含 .x6-graph 的 frame
    if target_frame is None:
        for f in page.frames:
            try:
                has_graph = await f.query_selector(".x6-graph")
                if has_graph:
                    target_frame = f
                    break
            except Exception:
                continue

    if target_frame is None:
        # 回退到主 frame
        target_frame = page.main_frame

    # 计算 iframe 在顶层页面的视口偏移
    offset_x, offset_y = 0.0, 0.0
    if target_frame != page.main_frame:
        try:
            frame_ele = await target_frame.frame_element()
            box = await frame_ele.bounding_box()
            if box:
                offset_x = float(box["x"])
                offset_y = float(box["y"])
        except Exception:
            pass

    # 执行 BIND 脚本
    raw = await target_frame.evaluate(f"() => {{ {BIND_X6} }}")
    meta = json.loads(raw) if isinstance(raw, str) else (raw or {})

    session = X6Session(
        page=page,
        frame=target_frame,
        offset_x=offset_x,
        offset_y=offset_y,
        meta=meta,
    )

    if auto_fit:
        try:
            await fit_view(session)
        except Exception:
            pass

    return session


async def fit_view(session: X6Session, padding: int = 40) -> dict[str, Any]:
    """自适应回正画布视口：开启平移并缩放居中。"""
    raw = await session.frame.evaluate(f"({FIT_VIEW})({padding})")
    res = json.loads(raw) if isinstance(raw, str) else (raw or {})
    await asyncio.sleep(0.15)
    return res


async def get_topology(session: X6Session, auto_fit: bool = True) -> dict[str, Any]:
    """提取 X6 流程图的拓扑结构、全部节点及端口信息（带顶层绝对视口坐标）。"""
    if auto_fit:
        await fit_view(session)

    # 1. 提取图模型拓扑
    raw = await session.frame.evaluate(f"() => {{ {EXTRACT_GRAPH} }}")
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    if not data.get("ok"):
        raise RuntimeError(f"提取 X6 画布数据失败: {data.get('reason', '未知原因')}")

    model_nodes = {n["id"]: n for n in data.get("nodes", [])}
    edges = data.get("edges", [])

    # 2. 扫描 DOM 节点与物理视口坐标
    dom_nodes_script = r"""() => {
        var nodes = [];
        var nodeEles = document.querySelectorAll('g.x6-node');
        for (var i = 0; i < nodeEles.length; i++) {
            var el = nodeEles[i];
            var cid = el.getAttribute('data-cell-id');
            var shape = el.getAttribute('data-shape');
            var txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
            var rect = el.getBoundingClientRect();

            var ports = {};
            var portEles = el.querySelectorAll('.x6-port-body');
            for (var j = 0; j < portEles.length; j++) {
                var p = portEles[j];
                var pid = p.getAttribute('port') || p.getAttribute('data-port-id') || ('port-' + j);
                var pgroup = p.getAttribute('port-group') || (pid.indexOf('out') >= 0 ? 'out' : 'in');
                var pr = p.getBoundingClientRect();
                ports[pid] = {
                    portId: pid,
                    group: pgroup,
                    center: { x: pr.left + pr.width / 2, y: pr.top + pr.height / 2 }
                };
            }

            nodes.append ? nodes.append : nodes.push({
                cellId: cid,
                shape: shape,
                text: txt,
                rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
                center: { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
                ports: ports
            });
        }
        return nodes;
    }"""
    dom_nodes_raw = await session.frame.evaluate(dom_nodes_script)
    dom_nodes = dom_nodes_raw if isinstance(dom_nodes_raw, list) else []

    resolved_nodes = []
    for dn in dom_nodes:
        cid = dn["cellId"]
        m_info = model_nodes.get(cid, {})

        # 换算到顶层视口绝对坐标
        vx, vy = session.to_viewport(dn["center"]["x"], dn["center"]["y"])
        v_rect_x, v_rect_y = session.to_viewport(dn["rect"]["x"], dn["rect"]["y"])

        # 端口坐标换算
        resolved_ports = {}
        for pid, p_data in dn.get("ports", {}).items():
            pvx, pvy = session.to_viewport(p_data["center"]["x"], p_data["center"]["y"])
            resolved_ports[pid] = {
                "portId": pid,
                "group": p_data["group"],
                "viewport_center": {"x": pvx, "y": pvy},
            }

        resolved_nodes.append({
            "cellId": cid,
            "shape": dn["shape"],
            "text": dn["text"],
            "kind": (m_info.get("data") or {}).get("kind"),
            "data": m_info.get("data"),
            "viewport_center": {"x": vx, "y": vy},
            "viewport_rect": {
                "x": v_rect_x,
                "y": v_rect_y,
                "width": dn["rect"]["width"],
                "height": dn["rect"]["height"],
            },
            "ports": resolved_ports,
        })

    c_rect = data.get("containerRect", {})
    c_vx, c_vy = session.to_viewport(c_rect.get("x", 0), c_rect.get("y", 0))

    return {
        "ok": True,
        "node_count": len(resolved_nodes),
        "edge_count": len(edges),
        "nodes": resolved_nodes,
        "edges": edges,
        "zoom": data.get("zoom", 1),
        "translate": data.get("translate", {"tx": 0, "ty": 0}),
        "container_viewport": {
            "x": c_vx,
            "y": c_vy,
            "width": c_rect.get("width", 0),
            "height": c_rect.get("height", 0),
        },
    }


def _match_node(nodes: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """按 cellId 或显示文本模糊匹配节点。"""
    clean = target.strip()
    # 1. 严格精确匹配 cellId
    for n in nodes:
        if n.get("cellId") == clean:
            return n
    # 2. 严格精确匹配文本
    for n in nodes:
        if n.get("text") == clean:
            return n
    # 3. 包含匹配
    for n in nodes:
        if clean in n.get("text", ""):
            return n
    return None


async def move_node(
    session: X6Session,
    node: str,
    dx: int,
    dy: int,
) -> dict[str, Any]:
    """拖拽移动指定的 X6 节点（基于 Playwright 平滑鼠标插值轨迹）。"""
    topo = await get_topology(session, auto_fit=False)
    matched = _match_node(topo["nodes"], node)
    if not matched:
        raise ValueError(f"未找到节点: {node!r}")

    # 起始点取节点内部安全交互点（偏左中，避开边缘连接桩）
    vr = matched["viewport_rect"]
    start_x = vr["x"] + vr["width"] * 0.3
    start_y = vr["y"] + vr["height"] * 0.5
    end_x = start_x + dx
    end_y = start_y + dy

    page = session.page
    # 模拟真实鼠标移动与拖拽
    await page.mouse.move(start_x, start_y)
    await page.wait_for_timeout(50)
    await page.mouse.down()
    await page.wait_for_timeout(50)

    # 12 步线性插值
    steps = 12
    for i in range(1, steps + 1):
        cur_x = start_x + (dx * i / steps)
        cur_y = start_y + (dy * i / steps)
        await page.mouse.move(cur_x, cur_y)
        await asyncio.sleep(0.015)

    await page.mouse.up()
    await page.wait_for_timeout(200)

    return {
        "ok": True,
        "node": matched["text"] or matched["cellId"],
        "cellId": matched["cellId"],
        "from": {"x": round(start_x, 1), "y": round(start_y, 1)},
        "to": {"x": round(end_x, 1), "y": round(end_y, 1)},
        "delta": {"dx": dx, "dy": dy},
    }


async def connect_nodes(
    session: X6Session,
    from_node: str,
    to_node: str,
    from_port: str = "out-0",
    to_port: str = "in-0",
) -> dict[str, Any]:
    """从源节点的出口桩拖拽物理连线至目标节点的入口桩。"""
    topo = await get_topology(session, auto_fit=False)
    src = _match_node(topo["nodes"], from_node)
    dst = _match_node(topo["nodes"], to_node)
    if not src:
        raise ValueError(f"未找到源节点: {from_node!r}")
    if not dst:
        raise ValueError(f"未找到目标节点: {to_node!r}")

    # 解析源端口
    src_ports = src.get("ports", {})
    src_p = src_ports.get(from_port)
    if not src_p:
        # 挑选任意可用 out 端口
        out_candidates = [p for p in src_ports.values() if p.get("group") == "out" or "out" in p.get("portId", "")]
        src_p = out_candidates[0] if out_candidates else next(iter(src_ports.values()), None)

    # 解析目标端口
    dst_ports = dst.get("ports", {})
    dst_p = dst_ports.get(to_port)
    if not dst_p:
        in_candidates = [p for p in dst_ports.values() if p.get("group") == "in" or "in" in p.get("portId", "")]
        dst_p = in_candidates[0] if in_candidates else next(iter(dst_ports.values()), None)

    if not src_p or not dst_p:
        raise ValueError(f"节点缺少可用连接桩。源端口: {list(src_ports.keys())}, 目标端口: {list(dst_ports.keys())}")

    start_pt = src_p["viewport_center"]
    end_pt = dst_p["viewport_center"]

    page = session.page
    await page.mouse.move(start_pt["x"], start_pt["y"])
    await page.wait_for_timeout(50)
    await page.mouse.down()
    await page.wait_for_timeout(50)

    # 平滑拉线
    steps = 15
    for i in range(1, steps + 1):
        cur_x = start_pt["x"] + ((end_pt["x"] - start_pt["x"]) * i / steps)
        cur_y = start_pt["y"] + ((end_pt["y"] - start_pt["y"]) * i / steps)
        await page.mouse.move(cur_x, cur_y)
        await asyncio.sleep(0.015)

    await page.mouse.move(end_pt["x"], end_pt["y"])
    await page.wait_for_timeout(50)
    await page.mouse.up()
    await page.wait_for_timeout(200)

    return {
        "ok": True,
        "from_node": src["text"] or src["cellId"],
        "to_node": dst["text"] or dst["cellId"],
        "from_port": src_p["portId"],
        "to_port": dst_p["portId"],
    }


async def click_node(
    session: X6Session,
    node: str,
    double: bool = False,
) -> dict[str, Any]:
    """单击选中或双击打开 X6 节点配置面板。"""
    topo = await get_topology(session, auto_fit=False)
    matched = _match_node(topo["nodes"], node)
    if not matched:
        raise ValueError(f"未找到节点: {node!r}")

    vr = matched["viewport_rect"]
    pt_x = vr["x"] + vr["width"] * 0.3
    pt_y = vr["y"] + vr["height"] * 0.5

    page = session.page
    if double:
        await page.mouse.dblclick(pt_x, pt_y)
    else:
        await page.mouse.click(pt_x, pt_y)

    await page.wait_for_timeout(250)
    return {
        "ok": True,
        "node": matched["text"] or matched["cellId"],
        "cellId": matched["cellId"],
        "action": "double_click" if double else "click",
        "point": {"x": round(pt_x, 1), "y": round(pt_y, 1)},
    }


async def delete_node(
    session: X6Session,
    node: str,
) -> dict[str, Any]:
    """选中并删除指定的 X6 节点（单击选中后派发 Backspace / Delete）。"""
    click_res = await click_node(session, node, double=False)
    page = session.page
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(100)
    await page.keyboard.press("Delete")
    await page.wait_for_timeout(200)

    return {
        "ok": True,
        "deleted_node": click_res["node"],
        "cellId": click_res["cellId"],
        "message": f"已选中节点 [{click_res['node']}] 并执行删除键",
    }
