"""Virtual mouse cursor visualization and smooth 60fps trajectory driver."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any

from .config import (
    _CURSOR_HEIGHT,
    _CURSOR_HOT_X,
    _CURSOR_HOT_Y,
    _CURSOR_WIDTH,
    _EMBEDDED_CURSOR_DATA_URL,
    SHOW_CURSOR,
    SHOW_DRAG_GHOST,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


_last_mouse_point: tuple[float, float] | None = None


def _reset_last_mouse_point() -> None:
    """Reset the cached mouse position when a browser session closes."""
    global _last_mouse_point
    _last_mouse_point = None


def _build_cursor_helper_script() -> str:
    cursor_css = f"""
    position: fixed;
    left: 0;
    top: 0;
    width: {_CURSOR_WIDTH}px;
    height: {_CURSOR_HEIGHT}px;
    pointer-events: none !important;
    z-index: 2147483647 !important;
    background-image: url("{_EMBEDDED_CURSOR_DATA_URL}");
    background-size: {_CURSOR_WIDTH}px {_CURSOR_HEIGHT}px;
    background-repeat: no-repeat;
    transform: translate3d(-100px, -100px, 0);
    will-change: transform;
    opacity: 0;
    transition: opacity 0.15s ease-out;
    transform-origin: {_CURSOR_HOT_X}px {_CURSOR_HOT_Y}px;
    filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.4));
"""
    ripple_css = """
    position: fixed;
    left: 0;
    top: 0;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 2px solid rgba(0, 120, 215, 0.7);
    background: rgba(0, 120, 215, 0.2);
    pointer-events: none !important;
    z-index: 2147483646 !important;
    transform: translate(-50%, -50%) scale(0);
    opacity: 0;
    transition: transform 0.25s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.25s ease-out;
"""
    update_pos = f"""
    window.__qa_automation_last_x = x;
    window.__qa_automation_last_y = y;
    const scale = isMouseDown ? ' scale(0.92)' : ' scale(1)';
    cursor.style.transform = `translate3d(${{x - {_CURSOR_HOT_X}}}px, ${{y - {_CURSOR_HOT_Y}}}px, 0)${{scale}}`;
"""
    return f"""(() => {{
  let cursor = document.getElementById('__qa_automation_cursor__');
  if (!cursor) {{
    cursor = document.createElement('div');
    cursor.id = '__qa_automation_cursor__';
    document.documentElement.appendChild(cursor);
  }}
  if (cursor.dataset.qaAutomationInstalled === '1' &&
      window.__qa_automation_update_cursor && window.__qa_automation_glide_cursor) {{
    return 'already-installed-win-cursor';
  }}
  cursor.dataset.qaAutomationInstalled = '1';
  cursor.innerHTML = '';
  cursor.style.cssText = `{cursor_css}`;

  let ripple = document.getElementById('__qa_automation_ripple__');
  if (!ripple) {{
    ripple = document.createElement('div');
    ripple.id = '__qa_automation_ripple__';
    document.documentElement.appendChild(ripple);
  }}
  ripple.style.cssText = `{ripple_css}`;
  window.__qa_automation_last_x = 0;
  window.__qa_automation_last_y = 0;
  let hideTimer = null;
  let safetyHideTimer = null;
  let isMouseDown = false;

  const scheduleHide = (delay = 2000) => {{
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {{
      if (!isMouseDown) {{
        cursor.style.opacity = '0';
      }}
    }}, delay);
    if (safetyHideTimer) clearTimeout(safetyHideTimer);
    safetyHideTimer = setTimeout(() => {{
      isMouseDown = false;
      cursor.style.opacity = '0';
    }}, Math.max(delay + 500, 2500));
  }};

  const showCursor = (stay = 3500) => {{
    cursor.style.opacity = '1';
    scheduleHide(stay);
  }};

  const updatePos = (x, y) => {{
    {update_pos}
  }};

  window.__qa_automation_hide_cursor = (delay = 0) => {{
    isMouseDown = false;
    if (hideTimer) clearTimeout(hideTimer);
    if (safetyHideTimer) clearTimeout(safetyHideTimer);
    if (delay <= 0) {{
      cursor.style.opacity = '0';
    }} else {{
      scheduleHide(delay);
    }}
  }};

  window.__qa_automation_update_cursor = (x, y, down = false, clickRipple = false, stayMs = 3500) => {{
    isMouseDown = down;
    updatePos(x, y);
    showCursor(stayMs);
    if (clickRipple) {{
      ripple.style.transition = 'none';
      ripple.style.left = x + 'px';
      ripple.style.top = y + 'px';
      ripple.style.transform = 'translate(-50%, -50%) scale(0.3)';
      ripple.style.opacity = '1';
      requestAnimationFrame(() => {{
        ripple.style.transition = 'transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out';
        ripple.style.transform = 'translate(-50%, -50%) scale(1.5)';
        ripple.style.opacity = '0';
      }});
    }}
  }};
  let activeGlideId = 0;
  window.__qa_automation_glide_cursor = (targetX, targetY, durationMs = 200) => {{
    return new Promise((resolve) => {{
      const currentGlideId = ++activeGlideId;
      const startX = (window.__qa_automation_last_x != null && window.__qa_automation_last_x > 0)
        ? window.__qa_automation_last_x
        : Math.max(0, targetX - 60);
      const startY = (window.__qa_automation_last_y != null && window.__qa_automation_last_y > 0)
        ? window.__qa_automation_last_y
        : Math.max(0, targetY - 40);

      const dx = targetX - startX;
      const dy = targetY - startY;
      const dist = Math.hypot(dx, dy);

      if (dist < 2 || durationMs <= 0) {{
        updatePos(targetX, targetY);
        showCursor(3500);
        resolve({{startX, startY, targetX, targetY, duration: 0}});
        return;
      }}

      showCursor(3500);
      const startTime = performance.now();

      function tick(now) {{
        if (activeGlideId !== currentGlideId) {{
          resolve({{cancelled: true}});
          return;
        }}
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / durationMs, 1);
        // Smooth cubic ease-out curve for high-FPS, natural hand glide
        const ease = 1 - Math.pow(1 - progress, 3);
        const currX = startX + dx * ease;
        const currY = startY + dy * ease;

        updatePos(currX, currY);

        if (progress < 1) {{
          requestAnimationFrame(tick);
        }} else {{
          updatePos(targetX, targetY);
          resolve({{startX, startY, targetX, targetY, duration: elapsed}});
        }}
      }}
      requestAnimationFrame(tick);
    }});
  }};

  // Note: Do not attach mousemove/mousedown/mouseup listeners to the window,
  // so real user mouse movements are never hijacked or mirrored by the virtual cursor.
  return 'installed-win-cursor';
}})()"""


_WIN_CURSOR_HELPER_SCRIPT = _build_cursor_helper_script()

_GHOST_START_SCRIPT = """([localX, localY]) => {
    let target = document.elementFromPoint(localX, localY);
    if (!target) return false;
    let draggable = target.closest('[draggable="true"]') ||
                    target.closest('.pro-approval-flow-panel-item, [class*="node"], [class*="card"], [class*="item"], button, [role="button"]');
    let el = draggable || target;
    if (!el || el === document.body || el === document.documentElement) return false;

    const rect = el.getBoundingClientRect();
    const existing = document.getElementById('__qa_automation_drag_ghost__');
    if (existing) {
        try { existing.remove(); } catch (e) {}
    }

    let ghost;
    if (el instanceof SVGElement && !(el instanceof SVGSVGElement)) {
        const svgWrapper = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svgWrapper.setAttribute('width', String(rect.width));
        svgWrapper.setAttribute('height', String(rect.height));
        svgWrapper.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
        const clonedG = el.cloneNode(true);
        clonedG.removeAttribute('transform');
        svgWrapper.appendChild(clonedG);
        ghost = document.createElement('div');
        ghost.appendChild(svgWrapper);
    } else {
        ghost = el.cloneNode(true);
    }

    ghost.id = '__qa_automation_drag_ghost__';
    ghost.style.cssText = `
        position: fixed !important;
        left: ${rect.left}px !important;
        top: ${rect.top}px !important;
        width: ${rect.width}px !important;
        height: ${rect.height}px !important;
        margin: 0 !important;
        pointer-events: none !important;
        z-index: 2147483645 !important;
        opacity: 0.92 !important;
        background: #ffffff !important;
        border: 2px solid #1890ff !important;
        border-radius: 6px !important;
        transform-origin: ${localX - rect.left}px ${localY - rect.top}px !important;
        transform: scale(1.05) !important;
        box-shadow: 0 14px 36px rgba(24, 144, 255, 0.38), 0 4px 12px rgba(0, 0, 0, 0.18) !important;
        transition: transform 0.05s linear, opacity 0.2s ease !important;
        user-select: none !important;
    `;
    document.body.appendChild(ghost);

    window.__qa_automation_ghost_state = {
        offsetX: localX - rect.left,
        offsetY: localY - rect.top
    };
    return true;
}"""

_GHOST_UPDATE_SCRIPT = """([x, y]) => {
    const ghost = document.getElementById('__qa_automation_drag_ghost__');
    const state = window.__qa_automation_ghost_state;
    if (!ghost || !state) return false;
    ghost.style.left = (x - state.offsetX) + 'px';
    ghost.style.top = (y - state.offsetY) + 'px';
    return true;
}"""

_GHOST_FINISH_SCRIPT = """([endX, endY]) => {
    const ghost = document.getElementById('__qa_automation_drag_ghost__');
    if (!ghost) return false;
    ghost.style.transition = 'transform 0.32s cubic-bezier(0.2, 0, 0, 1), opacity 0.32s ease-out';
    ghost.style.transform = 'scale(0.88)';
    ghost.style.opacity = '0';
    setTimeout(() => {
        try { ghost.remove(); } catch(e) {}
    }, 350);
    delete window.__qa_automation_ghost_state;
    return true;
}"""

_GHOST_CLEANUP_SCRIPT = """() => {
    const ghost = document.getElementById('__qa_automation_drag_ghost__');
    if (ghost) {
        try { ghost.remove(); } catch (e) {}
    }
    delete window.__qa_automation_ghost_state;
}"""


async def _ensure_cursor_helper(page: Page) -> None:
    """Ensure the Windows-style virtual mouse cursor helper is installed on the page."""
    if not SHOW_CURSOR:
        return
    try:
        if not page.url or page.url == "about:blank":
            return
        await asyncio.wait_for(page.evaluate(_WIN_CURSOR_HELPER_SCRIPT), timeout=1.0)
    except Exception:
        pass


async def _smooth_mouse_move_to(
    page: Page,
    target_x: float,
    target_y: float,
    settle_ms: int = 100,
) -> None:
    """Smoothly glide the mouse from its last known position to (target_x, target_y) and wait for it to settle."""
    global _last_mouse_point
    target_x, target_y = float(target_x), float(target_y)
    if not SHOW_CURSOR:
        # 无虚拟光标时轨迹仅为开销:单步直达即可获得同样的 trusted 事件
        await page.mouse.move(target_x, target_y)
        if settle_ms > 0:
            await asyncio.sleep(settle_ms / 1000.0)
        _last_mouse_point = (target_x, target_y)
        return
    start_x, start_y = (
        _last_mouse_point
        if _last_mouse_point
        else (max(0.0, target_x - 60), max(0.0, target_y - 40))
    )
    dx = target_x - start_x
    dy = target_y - start_y
    dist = math.hypot(dx, dy)

    # 动态根据位移距离计算优雅的滑行时长 (毫秒)，在 60-144 FPS 下提供细密流畅的高刷新轨迹
    duration_ms = max(70, min(280, int(70 + dist * 0.18)))

    glided = False
    try:
        if getattr(page, "url", None) and page.url != "about:blank":
            await _ensure_cursor_helper(page)
            # 在浏览器内核渲染层直接以 requestAnimationFrame (60-144 FPS) 硬件加速驱动平滑轨迹，彻底避免 Python 跨进程 IPC 掉帧
            await page.evaluate(
                f"window.__qa_automation_glide_cursor && window.__qa_automation_glide_cursor({target_x:.1f}, {target_y:.1f}, {duration_ms})"
            )
            glided = True
    except Exception:
        pass

    # 若未能在浏览器内核中以 rAF 执行（例如 mock 页面测试），则使用 Python 细密轨迹回退
    if not glided:
        steps = max(6, min(24, int(dist / 20)))
        for step in range(1, steps + 1):
            t = step / steps
            ease_t = 1 - math.pow(1 - t, 3)
            curr_x = start_x + dx * ease_t
            curr_y = start_y + dy * ease_t
            await page.mouse.move(curr_x, curr_y)

    # 确保落点精确到达目标坐标
    await page.mouse.move(target_x, target_y)
    if SHOW_CURSOR:
        try:
            await page.evaluate(
                f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({target_x:.1f}, {target_y:.1f}, false, false, 2000)"
            )
        except Exception:
            pass

    # 等待光标停稳 (settle)，确保 CSS 过渡与页面 hover/mouseenter 事件充分消化
    if settle_ms > 0:
        await asyncio.sleep(settle_ms / 1000.0)

    _last_mouse_point = (target_x, target_y)


async def _stable_viewport_click(
    page: Page,
    x: float,
    y: float,
    *,
    double_click: bool = False,
    button: str = "left",
    delay: float = 30.0,
    point_resolver: Any | None = None,
    move_to: Any | None = None,
) -> dict[str, float]:
    """Hover a point to stability, resolve its final location, then trusted-click it."""
    x, y = float(x), float(y)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("coordinates must be finite numbers")
    if button not in {"left", "middle", "right"}:
        raise ValueError("button must be left, middle or right")

    await _ensure_cursor_helper(page)
    await (move_to or _smooth_mouse_move_to)(page, x, y)
    if point_resolver is not None:
        refreshed = await point_resolver()
        if not refreshed:
            raise RuntimeError("target became unavailable after hover")
        x, y = float(refreshed["x"]), float(refreshed["y"])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("resolved coordinates must be finite numbers")

    if SHOW_CURSOR:
        try:
            await page.evaluate(
                f"window.__qa_automation_update_cursor && "
                f"window.__qa_automation_update_cursor({x:.1f}, {y:.1f}, true, true)"
            )
        except Exception:
            pass
    try:
        if double_click:
            await page.mouse.dblclick(x, y, button=button, delay=delay)
        else:
            await page.mouse.click(x, y, button=button, delay=delay)
    finally:
        if SHOW_CURSOR:
            try:
                await page.evaluate(
                    "window.__qa_automation_hide_cursor && "
                    "window.__qa_automation_hide_cursor(150)"
                )
            except Exception:
                pass
    return {"x": x, "y": y}


async def _mouse_drag_impl(
    page: Page,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    *,
    steps: int = 24,
    button: str = "left",
    hold_ms: int = 80,
    settle_ms: int = 200,
    visual_ghost: bool = True,
) -> dict[str, Any]:
    """Physically drag from start to end with sequential events and cursor feedback."""
    global _last_mouse_point
    start_x, start_y = float(start_x), float(start_y)
    end_x, end_y = float(end_x), float(end_y)
    if not (
        math.isfinite(start_x)
        and math.isfinite(start_y)
        and math.isfinite(end_x)
        and math.isfinite(end_y)
    ):
        raise ValueError("drag coordinates must be finite numbers")
    if button not in {"left", "middle", "right"}:
        raise ValueError("button must be left, middle or right")
    steps = max(4, min(100, int(steps)))

    from .browser import _page_viewport_size

    viewport = await _page_viewport_size(page)
    for name, x_val, y_val in (("start", start_x, start_y), ("end", end_x, end_y)):
        if not (0 <= x_val < viewport["width"] and 0 <= y_val < viewport["height"]):
            raise ValueError(
                f"{name} point ({x_val:g}, {y_val:g}) is outside viewport "
                f"({viewport['width']:g} x {viewport['height']:g})"
            )

    await _ensure_cursor_helper(page)
    await _smooth_mouse_move_to(page, start_x, start_y)

    cdp = None
    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception:
        pass

    try:
        ghost_frame = None
        ghost_offset = {"x": 0.0, "y": 0.0}
        if SHOW_DRAG_GHOST and visual_ghost:
            try:
                from .browser import _frame_page_offset

                # 仅在当前视口真实可见的子 iframe 中匹配，排查后台非激活 Tab
                for fr in page.frames:
                    if fr == page.main_frame:
                        continue
                    try:
                        handle = await fr.frame_element()
                        if not await handle.is_visible():
                            continue
                    except Exception:
                        continue
                    offset = await _frame_page_offset(page, fr)
                    lx = start_x - offset["x"]
                    ly = start_y - offset["y"]
                    has_target = await fr.evaluate(
                        """([x, y]) => {
                            let el = document.elementFromPoint(x, y);
                            if (!el || el === document.body || el === document.documentElement) return false;
                            if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') return false;
                            return true;
                        }""",
                        [lx, ly],
                    )
                    if has_target:
                        ghost_frame = fr
                        ghost_offset = offset
                        break
                if ghost_frame is None:
                    ghost_frame = page.main_frame
                    ghost_offset = {"x": 0.0, "y": 0.0}

                local_sx = start_x - ghost_offset["x"]
                local_sy = start_y - ghost_offset["y"]
                started = await ghost_frame.evaluate(_GHOST_START_SCRIPT, [local_sx, local_sy])
                if not started:
                    ghost_frame = None
            except Exception:
                ghost_frame = None

        # 检查起点和终点是否位于 HTML5 Draggable DOM 元素上（包括跨 iframe，仅在禁用视觉 Ghost 时尝试直达）
        html5_dragged = False
        if not visual_ghost:
            try:
                from .browser import _frame_page_offset

                for fr in page.frames:
                    offset = await _frame_page_offset(page, fr)
                    local_sx = start_x - offset["x"]
                    local_sy = start_y - offset["y"]
                    local_ex = end_x - offset["x"]
                    local_ey = end_y - offset["y"]

                    src_handle = await fr.evaluate_handle(
                        """([sx, sy]) => {
                        let el = document.elementFromPoint(sx, sy);
                        while (el && el !== document.body && el !== document.documentElement) {
                            if (el.getAttribute && (el.getAttribute('draggable') === 'true' || el.hasAttribute('draggable'))) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                        return null;
                    }""",
                        [local_sx, local_sy],
                    )

                    dst_handle = await fr.evaluate_handle(
                        """([ex, ey]) => {
                        let el = document.elementFromPoint(ex, ey);
                        while (el && el !== document.body && el !== document.documentElement) {
                            if (el.getAttribute && (el.getAttribute('draggable') === 'true' || el.hasAttribute('draggable'))) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                        return null;
                    }""",
                        [local_ex, local_ey],
                    )

                    src_elem = src_handle.as_element()
                    dst_elem = dst_handle.as_element()

                    if src_elem and dst_elem and src_elem != dst_elem:
                        if SHOW_CURSOR:
                            try:
                                await page.evaluate(
                                    f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({start_x:.1f}, {start_y:.1f}, true, true)"
                                )
                            except Exception:
                                pass

                        await src_elem.drag_to(dst_elem, timeout=5000)

                        if SHOW_CURSOR:
                            try:
                                await page.evaluate(
                                    f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({end_x:.1f}, {end_y:.1f}, false, false)"
                                )
                            except Exception:
                                pass

                        _last_mouse_point = (end_x, end_y)
                        html5_dragged = True
                        break
            except Exception:
                pass

        # 若非直达（如 Canvas VTable 列表头、滑块或启用视觉 Ghost），走高精度 60fps 物理鼠标平滑轨迹
        if not html5_dragged:
            # 1. 鼠标平滑移动至起点并按下左键 (mouse.down)
            if SHOW_CURSOR:
                try:
                    await page.evaluate(
                        f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({start_x:.1f}, {start_y:.1f}, true, true)"
                    )
                except Exception:
                    pass

            await page.mouse.move(start_x, start_y)
            await page.mouse.down(button=button)

            if hold_ms > 0:
                await asyncio.sleep(hold_ms / 1000)

            # 2. 连续步进插值平滑物理拖拽轨迹 (mouse.move)
            dx = end_x - start_x
            dy = end_y - start_y
            for step in range(1, steps + 1):
                t = step / steps
                # 三次缓动曲线增强真实物理拖拽手感
                ease_t = 3 * t * t - 2 * t * t * t
                curr_x = start_x + dx * ease_t
                curr_y = start_y + dy * ease_t
                if SHOW_CURSOR:
                    try:
                        await page.evaluate(
                            f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({curr_x:.1f}, {curr_y:.1f}, true, false)"
                        )
                    except Exception:
                        pass
                if ghost_frame is not None:
                    try:
                        local_cx = curr_x - ghost_offset["x"]
                        local_cy = curr_y - ghost_offset["y"]
                        await ghost_frame.evaluate(_GHOST_UPDATE_SCRIPT, [local_cx, local_cy])
                    except Exception:
                        pass

                await page.mouse.move(curr_x, curr_y)
                await asyncio.sleep(0.016)

            # 3. 终点释放 (mouse.up)
            if hold_ms > 0:
                await asyncio.sleep(hold_ms / 1000)
            await page.mouse.up(button=button)

            if ghost_frame is not None:
                try:
                    local_ex = end_x - ghost_offset["x"]
                    local_ey = end_y - ghost_offset["y"]
                    await ghost_frame.evaluate(_GHOST_FINISH_SCRIPT, [local_ex, local_ey])
                except Exception:
                    pass

            if SHOW_CURSOR:
                try:
                    await page.evaluate(
                        f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({end_x:.1f}, {end_y:.1f}, false, false)"
                    )
                except Exception:
                    pass
            _last_mouse_point = (end_x, end_y)
    finally:
        if ghost_frame is not None:
            try:
                await ghost_frame.evaluate(_GHOST_CLEANUP_SCRIPT)
            except Exception:
                pass
        if cdp is not None:
            try:
                await cdp.detach()
            except Exception:
                pass
        if SHOW_CURSOR:
            try:
                await page.evaluate(
                    "window.__qa_automation_hide_cursor && window.__qa_automation_hide_cursor(0)"
                )
            except Exception:
                pass
    if settle_ms > 0:
        await asyncio.sleep(settle_ms / 1000)

    return {
        "status": "dragged",
        "start": {"x": round(start_x, 2), "y": round(start_y, 2)},
        "end": {"x": round(end_x, 2), "y": round(end_y, 2)},
        "distance": round(math.hypot(end_x - start_x, end_y - start_y), 2),
        "steps": steps,
        "button": button,
        "channel": "cdp" if cdp is not None else "playwright-mouse",
        "coordinate_space": "top-page-viewport-css-pixels",
        "visual_ghost": ghost_frame is not None,
    }
