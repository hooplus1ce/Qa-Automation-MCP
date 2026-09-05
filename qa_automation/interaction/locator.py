"""Semantic locator resolution and Ant Design dropdown option selection."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

from ..browser import _frame_details
from ..components.vtable.binding import active_application_frame, resolve_frame
from ..config import ACTIVE_PROFILE, LOCATOR_STRATEGY


async def _unique_visible_locator(locator: Any, source: str) -> Any | None:
    """Return the sole visible candidate or reject an ambiguous locator."""
    visible: list[Any] = []
    labels: list[str] = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        try:
            if not await candidate.is_visible():
                continue
            visible.append(candidate)
            labels.append(" ".join((await candidate.inner_text()).split())[:80])
        except Exception:
            continue
    if len(visible) == 1:
        return visible[0]
    if len(visible) > 1:
        summary = [label or f"candidate-{index}" for index, label in enumerate(labels)]
        raise ValueError(
            f"ambiguous {source} locator: {len(visible)} visible matches: {summary[:20]}"
        )
    return None


async def _find_interaction_locator(
    page: Page,
    *,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    timeout_ms: float = 3_000,
) -> tuple[Any, Frame, str]:
    if not any([role, text, placeholder, css, xpath]):
        raise ValueError("one locator is required: role, text, placeholder, css or xpath")
    if frame is not None:
        frames = [await resolve_frame(page, frame)]
    else:
        frames = []
        if in_iframe:
            active = await active_application_frame(page)
            if active is not None:
                frames.append(active)
        frames.append(page.main_frame)

    for candidate in frames:
        available: dict[str, Any] = {}
        if css:
            available["css"] = candidate.locator(css)
        if role:
            kwargs = {"name": name, "exact": True} if name else {}
            if description:
                kwargs["description"] = description
            available["ax-role"] = candidate.get_by_role(role, **kwargs)
        if xpath:
            available["xpath"] = candidate.locator(f"xpath={xpath}")
        if text:
            available["text"] = candidate.get_by_text(text, exact=True)
        if placeholder:
            available["placeholder"] = candidate.get_by_placeholder(placeholder, exact=True)
        locators = [
            (source, available[source])
            for source in LOCATOR_STRATEGY.order
            if source in available
        ]
        for source, locator in locators:
            try:
                unique = await _unique_visible_locator(locator, source)
            except ValueError:
                raise
            except Exception:
                continue
            if unique is not None:
                return unique, candidate, source
    raise ValueError("target control not found in the selected page/frame scope")


async def _visible_antd_dropdown(target: Any) -> Any | None:
    selector = ",".join(ACTIVE_PROFILE.dropdown_selectors)
    locator = target.locator(selector)
    try:
        for index in range(await locator.count() - 1, -1, -1):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


async def _click_unique_antd_option(
    dropdown: Any, option_text: str, *, timeout_ms: float
) -> dict[str, Any] | None:
    for role in ("option", "menuitem", "treeitem"):
        locator = dropdown.get_by_role(role, name=option_text, exact=True)
        try:
            visible = [
                locator.nth(index)
                for index in range(await locator.count())
                if await locator.nth(index).is_visible()
            ]
        except Exception:
            visible = []
        if len(visible) == 1:
            await visible[0].click(timeout=timeout_ms)
            return {"match": "exact-role", "role": role, "text": option_text}
        if len(visible) > 1:
            raise ValueError(
                f"AntD option {option_text!r} has {len(visible)} exact {role} matches"
            )

    candidates = dropdown.locator(ACTIVE_PROFILE.dropdown_option_selector)
    visible_candidates: list[tuple[Any, str]] = []
    try:
        count = min(await candidates.count(), 200)
        for index in range(count):
            candidate = candidates.nth(index)
            if not await candidate.is_visible():
                continue
            text_value = " ".join((await candidate.inner_text()).split())
            if text_value:
                visible_candidates.append((candidate, text_value))
    except Exception:
        return None

    exact = [item for item in visible_candidates if item[1] == option_text]
    if len(exact) == 1:
        await exact[0][0].click(timeout=timeout_ms)
        return {"match": "exact-text", "text": exact[0][1]}
    if len(exact) > 1:
        raise ValueError(
            f"AntD option {option_text!r} has multiple exact matches: "
            f"{sorted({text for _, text in exact})[:20]}"
        )

    partial = [item for item in visible_candidates if option_text in item[1]]
    if len(partial) == 1:
        await partial[0][0].click(timeout=timeout_ms)
        return {"match": "partial-text", "text": partial[0][1]}
    if len(partial) > 1:
        raise ValueError(
            f"AntD option {option_text!r} is ambiguous; candidates: "
            f"{sorted({text for _, text in partial})[:20]}"
        )
    return None


async def _perform_antd_select(
    page: Page,
    target_frame: Frame,
    locator: Any,
    option_text: str,
    *,
    timeout_ms: float,
) -> dict[str, Any]:
    first = locator.first
    component = await first.evaluate(
        """element => {
          const root = element.closest('.ant-select, .ant-cascader, .ant-tree-select');
          if (!root) return null;
          if (root.classList.contains('ant-cascader')) return 'antd-cascader';
          if (root.classList.contains('ant-tree-select')) return 'antd-tree-select';
          return 'antd-select';
        }"""
    )
    if not component:
        raise ValueError("target is not an Ant Design select component")
    before_text = ""
    try:
        before_text = " ".join((await first.inner_text()).split())[:200]
    except Exception:
        pass
    await first.click(timeout=timeout_ms)

    targets: list[Any] = [target_frame]
    if target_frame != page.main_frame:
        targets.append(page.main_frame)
    deadline = time.monotonic() + max(0.2, timeout_ms / 1000)
    while time.monotonic() < deadline:
        for target in targets:
            dropdown = await _visible_antd_dropdown(target)
            if dropdown is None:
                continue
            matched = await _click_unique_antd_option(
                dropdown, option_text, timeout_ms=max(200, timeout_ms)
            )
            if matched:
                after_text = ""
                try:
                    after_text = " ".join((await first.inner_text()).split())[:200]
                except Exception:
                    pass
                return {
                    "component": component,
                    "option": option_text,
                    "match": matched,
                    "trigger_text_before": before_text,
                    "trigger_text_after": after_text,
                    "portal_frame": _frame_details(page, target),
                }
        await page.wait_for_timeout(100)
    raise ValueError(f"AntD option not found: {option_text!r}")
