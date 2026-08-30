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
    transform: translate(-100px, -100px);
    opacity: 0;
    transition: opacity 0.15s ease-out, transform 0.02s linear;
    transform-origin: {_CURSOR_HOT_X}px {_CURSOR_HOT_Y}px;
    filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.4));
"""
    update_pos = f"""
    window.__qa_automation_last_x = x;
    window.__qa_automation_last_y = y;
    const scale = isMouseDown ? ' scale(0.92)' : ' scale(1)';
    cursor.style.transform = `translate(${{x - {_CURSOR_HOT_X}}}px, ${{y - {_CURSOR_HOT_Y}}}px)${{scale}}`;
"""
    return f"""(() => {{
  if (document.getElementById('__qa_automation_cursor__')) return 'already-installed';

  const cursor = document.createElement('div');
  cursor.id = '__qa_automation_cursor__';
  cursor.innerHTML = '';
  cursor.style.cssText = `{cursor_css}`;

  const ripple = document.createElement('div');
  ripple.id = '__qa_automation_ripple__';
  ripple.style.cssText = `
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
  `;

  document.documentElement.appendChild(cursor);
  document.documentElement.appendChild(ripple);

  window.__qa_automation_last_x = 0;
  window.__qa_automation_last_y = 0;
  let hideTimer = null;
  let safetyHideTimer = null;
  let isMouseDown = false;

  const scheduleHide = (delay = 200) => {{
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
    }}, Math.max(delay + 300, 600));
  }};

  const showCursor = () => {{
    cursor.style.opacity = '1';
    scheduleHide(250);
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

  window.__qa_automation_update_cursor = (x, y, down = false, clickRipple = false) => {{
    isMouseDown = down;
    updatePos(x, y);
    if (down) {{
      showCursor();
    }} else {{
      scheduleHide(150);
    }}
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

  // Note: Do not attach mousemove/mousedown/mouseup listeners to the window,
  // so real user mouse movements are never hijacked or mirrored by the virtual cursor.
  return 'installed-win-cursor';
}})()"""


_WIN_CURSOR_HELPER_SCRIPT = _build_cursor_helper_script()


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
    page: Page, target_x: float, target_y: float
) -> None:
    """Smoothly glide the mouse from its last known position to (target_x, target_y) at >= 60fps."""
    global _last_mouse_point
    target_x, target_y = float(target_x), float(target_y)
    if not SHOW_CURSOR:
        # 无虚拟光标时轨迹仅为开销:单步直达即可获得同样的 trusted 事件
        await page.mouse.move(target_x, target_y)
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

    steps = max(10, min(35, int(dist / 22)))
    for step in range(1, steps + 1):
        t = step / steps
        ease_t = 1 - math.pow(1 - t, 3)
        curr_x = start_x + dx * ease_t
        curr_y = start_y + dy * ease_t
        if SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({curr_x:.1f}, {curr_y:.1f})"
                )
            except Exception:
                pass
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(0.016)
    _last_mouse_point = (target_x, target_y)

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
) -> dict[str, Any]:
    """Physically drag from start to end with sequential events and cursor feedback."""
    global _last_mouse_point
    start_x, start_y = float(start_x), float(start_y)
    end_x, end_y = float(end_x), float(end_y)
    if not (math.isfinite(start_x) and math.isfinite(start_y) and math.isfinite(end_x) and math.isfinite(end_y)):
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

    button_flag = 1 if button == "left" else (2 if button == "right" else 4)

    try:
        # 1. 鼠标在起点按下 (mousePressed)
        if SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({start_x:.1f}, {start_y:.1f}, true, true)"
                )
            except Exception:
                pass

        if cdp is not None:
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": start_x,
                    "y": start_y,
                    "button": button,
                    "clickCount": 1,
                    "buttons": button_flag,
                },
            )
        else:
            await page.mouse.down(button=button)

        if hold_ms > 0:
            await asyncio.sleep(hold_ms / 1000)

        # 2. 连续步进插值平滑轨迹 (mouseMoved)
        dx = end_x - start_x
        dy = end_y - start_y
        for step in range(1, steps + 1):
            t = step / steps
            # 三次缓动曲线增强拖拽逼真度
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

            if cdp is not None:
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseMoved",
                        "x": curr_x,
                        "y": curr_y,
                        "buttons": button_flag,
                    },
                )
            else:
                await page.mouse.move(curr_x, curr_y)
            await asyncio.sleep(0.012)

        # 3. 终点释放 (mouseReleased)
        if cdp is not None:
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": end_x,
                    "y": end_y,
                    "button": button,
                    "clickCount": 1,
                },
            )
        else:
            await page.mouse.up(button=button)

        if SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__qa_automation_update_cursor && window.__qa_automation_update_cursor({end_x:.1f}, {end_y:.1f}, false, false)"
                )
            except Exception:
                pass
        _last_mouse_point = (end_x, end_y)
    finally:
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
    }
