"""Virtual mouse cursor visualization and smooth 60fps trajectory driver."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

from .config import (
    _CURSOR_HEIGHT,
    _CURSOR_HOT_X,
    _CURSOR_HOT_Y,
    _CURSOR_WIDTH,
    _EMBEDDED_CURSOR_DATA_URL,
    VTABLE_SHOW_CURSOR,
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
    const scale = isMouseDown ? ' scale(0.92)' : ' scale(1)';
    cursor.style.transform = `translate(${{x - {_CURSOR_HOT_X}}}px, ${{y - {_CURSOR_HOT_Y}}}px)${{scale}}`;
"""

    return f"""(() => {{
  if (document.getElementById('__vtable_win_cursor__')) return 'already-installed';

  const cursor = document.createElement('div');
  cursor.id = '__vtable_win_cursor__';
  cursor.innerHTML = '';
  cursor.style.cssText = `{cursor_css}`;

  const ripple = document.createElement('div');
  ripple.id = '__vtable_win_ripple__';
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

  let hideTimer = null;
  let isMouseDown = false;

  const scheduleHide = () => {{
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {{
      if (!isMouseDown) {{
        cursor.style.opacity = '0';
      }}
    }}, 150);
  }};

  const showCursor = () => {{
    cursor.style.opacity = '1';
    scheduleHide();
  }};

  const updatePos = (x, y) => {{
    {update_pos}
  }};

  window.__vtable_update_cursor = (x, y, down = false, clickRipple = false) => {{
    isMouseDown = down;
    showCursor();
    updatePos(x, y);
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

  window.addEventListener('mousemove', e => {{
    showCursor();
    updatePos(e.clientX, e.clientY);
  }}, true);

  window.addEventListener('mousedown', e => {{
    isMouseDown = true;
    showCursor();
    updatePos(e.clientX, e.clientY);
    ripple.style.transition = 'none';
    ripple.style.transform = `translate(${{e.clientX}}px, ${{e.clientY}}px) scale(0.4)`;
    ripple.style.opacity = '1';
    requestAnimationFrame(() => {{
      ripple.style.transition = 'transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out';
      ripple.style.transform = `translate(${{e.clientX}}px, ${{e.clientY}}px) scale(1.4)`;
      ripple.style.opacity = '0';
    }});
  }}, true);

  window.addEventListener('mouseup', e => {{
    isMouseDown = false;
    showCursor();
    updatePos(e.clientX, e.clientY);
  }}, true);

  return 'installed-win-cursor';
}})()"""


_WIN_CURSOR_HELPER_SCRIPT = _build_cursor_helper_script()


async def _ensure_cursor_helper(page: Page) -> None:
    """Ensure the Windows-style virtual mouse cursor helper is installed on the page."""
    if not VTABLE_SHOW_CURSOR:
        return
    try:
        await page.evaluate(_WIN_CURSOR_HELPER_SCRIPT)
    except Exception:
        pass


async def _smooth_mouse_move_to(
    page: Page, target_x: float, target_y: float
) -> None:
    """Smoothly glide the mouse from its last known position to (target_x, target_y) at >= 60fps."""
    global _last_mouse_point
    target_x, target_y = float(target_x), float(target_y)
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
        if VTABLE_SHOW_CURSOR:
            try:
                await page.evaluate(
                    f"window.__vtable_update_cursor && window.__vtable_update_cursor({curr_x:.1f}, {curr_y:.1f})"
                )
            except Exception:
                pass
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(0.016)
    _last_mouse_point = (target_x, target_y)
