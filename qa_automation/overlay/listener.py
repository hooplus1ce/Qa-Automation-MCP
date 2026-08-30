"""Frame lifecycle listener for cross-frame Ant Design overlay tracking."""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ..browser import _frame_details


class _OverlayFrameListener:
    """Keep newly attached/navigated iframe documents covered during an action."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.events: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active = False

    def attach(self) -> None:
        if self._active:
            return
        self._active = True
        self.page.on("frameattached", self._on_frame)
        self.page.on("framenavigated", self._on_frame)

    def _on_frame(self, frame: Frame) -> None:
        if not self._active:
            return
        try:
            task = asyncio.create_task(self._install(frame))
        except RuntimeError as exc:  # pragma: no cover - loop shutdown race
            self.errors.append({"reason": f"observer-frame-task-error: {exc}"})
            return
        self._tasks.add(task)

    async def _install(self, frame: Frame) -> None:
        from . import _install_overlay_observer_in_frame
        try:
            result = await _install_overlay_observer_in_frame(self.page, frame, reset=False)
            if not result["reused"]:
                self.events.extend({**item, "event": "added"} for item in result["baseline"])
        except Exception as exc:
            try:
                details = _frame_details(self.page, frame)
            except Exception:
                details = {"frame_id": "", "frame_url": "", "frame_name": ""}
            self.errors.append({**details, "reason": str(exc)[:500]})

    async def wait_pending(self) -> None:
        if not self._tasks:
            return
        pending = tuple(self._tasks)
        await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.difference_update(pending)

    def take_buffers(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events, errors = self.events, self.errors
        self.events = []
        self.errors = []
        return events, errors

    async def close(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            self.page.remove_listener("frameattached", self._on_frame)
            self.page.remove_listener("framenavigated", self._on_frame)
        except Exception as exc:
            self.errors.append({"reason": f"observer-listener-remove-error: {exc}"})
        await self.wait_pending()


_overlay_frame_listeners: weakref.WeakKeyDictionary[Any, _OverlayFrameListener] = (
    weakref.WeakKeyDictionary()
)


async def _acquire_overlay_frame_listener(
    page: Page, *, persistent: bool
) -> tuple[_OverlayFrameListener, bool]:
    """Acquire the page listener, replacing a prior action-scoped listener."""
    existing = _overlay_frame_listeners.get(page)
    if existing is not None and existing._active:
        if persistent:
            return existing, False
        await existing.close()
        _overlay_frame_listeners.pop(page, None)
    listener = _OverlayFrameListener(page)
    listener.attach()
    if persistent:
        _overlay_frame_listeners[page] = listener
    return listener, True


async def _release_overlay_frame_listener(
    page: Page, listener: _OverlayFrameListener | None, *, persistent: bool
) -> list[dict[str, Any]]:
    if listener is None or persistent:
        return []
    errors: list[dict[str, Any]] = []
    try:
        await listener.close()
    except Exception as exc:
        errors.append({"reason": f"observer-listener-close-error: {exc}"})
    if _overlay_frame_listeners.get(page) is listener:
        _overlay_frame_listeners.pop(page, None)
    return errors
