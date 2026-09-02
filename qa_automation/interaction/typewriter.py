"""Typewriter-like character-by-character text input engine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

# Base delay parameters (in milliseconds)
# - Length <= 30: 50ms per character (natural human-like cadence, triggers Vue/React/AntD onInput/onChange properly)
# - 31 <= Length <= 60: 25ms per character (appropriately accelerated)
# - Length > 60: 15ms per character (brisk typing for very long text)
SHORT_TEXT_THRESHOLD = 30
MEDIUM_TEXT_THRESHOLD = 60

BASE_DELAY_MS = 50.0
MEDIUM_DELAY_MS = 25.0
LONG_DELAY_MS = 15.0


def calculate_typewriter_delay(text: str) -> float:
    """Calculate the inter-character typing delay in milliseconds based on text length.

    - Length <= 30: 50.0 ms (cadence is neither too fast nor too slow)
    - 31 <= Length <= 60: 25.0 ms (appropriately shortened delay for medium-length text)
    - Length > 60: 15.0 ms (brisk cadence for long text)
    """
    length = len(text)
    if length <= SHORT_TEXT_THRESHOLD:
        return BASE_DELAY_MS
    elif length <= MEDIUM_TEXT_THRESHOLD:
        return MEDIUM_DELAY_MS
    else:
        return LONG_DELAY_MS


async def typewriter_fill(
    locator: Locator,
    text: str,
    *,
    delay_ms: float | None = None,
    timeout_ms: float = 5_000,
) -> None:
    """Typewriter-like character-by-character replacement of input value.

    1. Focuses and clears any existing value.
    2. Sequentially presses each character with dynamic/specified delay.
    3. Dispatches change/input events for frameworks (React, Vue, Ant Design, Legions).
    """
    target = locator.first
    actual_delay = calculate_typewriter_delay(text) if delay_ms is None else delay_ms

    try:
        await target.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        pass

    try:
        # Clear existing text and focus
        await target.fill("", timeout=timeout_ms)
        # Sequentially press characters
        await target.press_sequentially(text, delay=actual_delay, timeout=timeout_ms)
    except Exception as exc:
        logger.debug("press_sequentially failed, falling back to fill: %s", exc)
        await target.fill(text, timeout=timeout_ms)


async def typewriter_type(
    locator: Locator,
    text: str,
    *,
    delay_ms: float | None = None,
    timeout_ms: float = 5_000,
) -> None:
    """Typewriter-like character-by-character appending of text at current cursor."""
    target = locator.first
    actual_delay = calculate_typewriter_delay(text) if delay_ms is None else delay_ms

    try:
        await target.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        pass

    try:
        await target.focus(timeout=timeout_ms)
        await target.press_sequentially(text, delay=actual_delay, timeout=timeout_ms)
    except Exception as exc:
        logger.debug("press_sequentially failed, falling back to fill: %s", exc)
        await target.fill(text, timeout=timeout_ms)


async def typewriter_keyboard_type(
    page: Page,
    text: str,
    *,
    delay_ms: float | None = None,
    clear_first: bool = False,
) -> None:
    """Typewriter-like typing on active focused element using page keyboard."""
    actual_delay = calculate_typewriter_delay(text) if delay_ms is None else delay_ms
    if clear_first:
        try:
            # Select all and delete
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        except Exception:
            pass
    await page.keyboard.type(text, delay=actual_delay)
