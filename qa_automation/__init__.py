"""General-purpose UI automation framework.

The public facade covers browser lifecycle, semantic DOM interaction, overlay
observation, screenshots, and optional component adapters. VTable-specific
support is isolated under :mod:`qa_automation.components.vtable`.
"""

from __future__ import annotations

from typing import Any

from .browser import (
    _action_lock,
    _browser_session_impl,
    _chrome_executable,
    _close_browser_impl,
    _connect_browser_impl,
    _context_id,
    _current_page_impl,
    _frame_context_details,
    _frame_details,
    _frame_id,
    _frame_name_url,
    _frame_page_offset,
    _launch_chrome_impl,
    _list_pages_impl,
    _open_url_impl,
    _page_id,
    _page_viewport_size,
    _select_page_impl,
    _select_page_object,
    _session_summary,
    _start_browser_impl,
    _state,
    _wait_for_cdp,
    browser_login,
    browser_session,
    close_browser,
    connect_browser,
    current_page,
    launch_chrome,
    list_pages,
    open_url,
    reset_viewport,
    select_page,
    set_page_preference_probe,
    start_browser,
)
from .components.vtable import (
    _cells_read_impl,
    _click_cell_impl,
    _do_click,
    _drop_files_impl,
    _table_meta_impl,
    _trusted_viewport_click,
    cell_info,
    cells_read,
    click_cell,
    click_vtable_cell_by_field,
    drop_files,
    table_meta,
)
from .components.vtable.analysis import (
    _analysis_cache,
    _analysis_counter,
    _analysis_geometry,
    _analysis_layout_signature,
    _remember_analysis,
    _vtable_analysis_impl,
    vtable_analysis,
)
from .components.vtable.binding import (
    _cell_visible_js,
    _resolve_vtable_cell_impl,
    _vtable_directory,
    _vtable_discover_impl,
    _wrap,
    _wrap2,
    active_application_frame,
    cell_center,
    cell_offset,
    cell_visible,
    discover_vtables,
    ensure_cell_visible,
    ensure_vtable,
    resolve_frame,
    resolve_vtable_cell,
    vtable_frame,
)
from .components.vtable.verification import (
    _CELL_CANVAS_SLICE_JS,
    _cell_screenshot,
    _cell_visual_state,
    _verify_landed,
)
from .config import (
    _CURSOR_HEIGHT,
    _CURSOR_HOT_X,
    _CURSOR_HOT_Y,
    _CURSOR_WIDTH,
    _EMBEDDED_CURSOR_DATA_URL,
    _EMBEDDED_CURSOR_PNG_BASE64,
    ACTIVE_IFRAME_SELECTOR,
    ACTIVE_PROFILE,
    ANALYSIS_CACHE_LIMIT,
    ANALYSIS_MAX_AGE_SECONDS,
    ANTD_OVERLAY_SELECTOR,
    BIND_TIMEOUT_MS,
    LOCATOR_STRATEGY,
    NAV_TIMEOUT_MS,
    OVERLAY_EVENT_LIMIT,
    OVERLAY_OBSERVER_KEY,
    OVERLAY_RESULT_LIMIT,
    OVERLAY_SETTLE_LIMIT_MS,
    PLAYWRIGHT_INSTALL_HINT,
    QA_AUTOMATION_PROJECT_ROOT,
    SCROLL_WAIT_RAF,
    SETTLE_MS,
    SHOW_CURSOR,
    VTABLE_VERIFICATION_STRATEGY,
    _env_bool,
    _env_int,
    active_profile,
)
from .interaction import (
    _click_dom_impl,
    _dom_interact_impl,
    _perform_dom_action,
    click_dom,
    dom_interact,
    mouse_drag,
)
from .interaction.contract import _interaction_contract
from .interaction.locator import (
    _click_unique_antd_option,
    _find_interaction_locator,
    _perform_antd_select,
    _visible_antd_dropdown,
)
from .interaction.snapshot import (
    _COMPACT_CONTROL_SCAN,
    _analyze_scope_impl,
    _dom_snapshot_impl,
    _focused_editable,
    _page_context_impl,
    _screenshot_element_impl,
    analyze_scope,
    dom_snapshot,
    page_context,
    screenshot_element,
)
from .interaction.typewriter import (
    calculate_typewriter_delay,
    typewriter_fill,
    typewriter_keyboard_type,
    typewriter_type,
)
from .mouse import (
    _WIN_CURSOR_HELPER_SCRIPT,
    _build_cursor_helper_script,
    _ensure_cursor_helper,
    _last_mouse_point,
    _reset_last_mouse_point,
    _smooth_mouse_move_to,
    _stable_viewport_click,
)
from .overlay import (
    _drain_overlay_observers,
    _finalize_overlay_observation,
    _install_overlay_observer_in_frame,
    _install_overlay_observers,
    _observe_overlays_impl,
    _page_frame_count,
    _scan_overlays_impl,
    _stop_overlay_observers_best_effort,
    click_dom_and_observe,
    observe_overlays,
    scan_overlays,
)
from .overlay.enrichment import (
    _OVERLAY_PRIORITY,
    _dedupe_overlays,
    _enrich_overlay_items,
    _filter_overlay_scope,
    _new_overlays,
    _overlay_context,
    _overlay_sort_key,
    _scope_frame_ids,
)
from .overlay.listener import (
    _acquire_overlay_frame_listener,
    _overlay_frame_listeners,
    _OverlayFrameListener,
    _release_overlay_frame_listener,
)
from .overlay.scripts import (
    _OVERLAY_ARM_INIT_SCRIPT,
    _OVERLAY_DRAIN_TEMPLATE,
    _OVERLAY_OBSERVER_TEMPLATE,
    _overlay_arm_script,
    _overlay_script,
)


async def _vtable_page_preference_probe(page: Any) -> bool:
    """组合层装配的页面偏好探测:优先含活动 iframe 或 VTable 的页面。"""
    from .components.vtable.binding import active_application_frame, vtable_frame

    if await active_application_frame(page) is not None:
        return True
    fr = await vtable_frame(page)
    return fr != page.main_frame or bool(await fr.locator(".vtable").count())


set_page_preference_probe(_vtable_page_preference_probe)

__all__ = [
    # Config & Profiles
    "ACTIVE_IFRAME_SELECTOR",
    "ACTIVE_PROFILE",
    "ANTD_OVERLAY_SELECTOR",
    "LOCATOR_STRATEGY",
    "NAV_TIMEOUT_MS",
    "OVERLAY_OBSERVER_KEY",
    "OVERLAY_RESULT_LIMIT",
    "QA_AUTOMATION_PROJECT_ROOT",
    "SHOW_CURSOR",
    "VTABLE_VERIFICATION_STRATEGY",
    "active_profile",
    # Browser & Navigation
    "start_browser",
    "connect_browser",
    "close_browser",
    "launch_chrome",
    "current_page",
    "list_pages",
    "select_page",
    "browser_session",
    "open_url",
    "browser_login",
    "reset_viewport",
    # VTable
    "vtable_frame",
    "active_application_frame",
    "resolve_frame",
    "ensure_vtable",
    "cell_center",
    "cell_info",
    "click_cell",
    "resolve_vtable_cell",
    "click_vtable_cell_by_field",
    "table_meta",
    "cells_read",
    "drop_files",
    "vtable_analysis",
    "discover_vtables",
    # DOM Interaction
    "click_dom",
    "click_dom_and_observe",
    "dom_interact",
    "mouse_drag",
    # Scope & Overlays
    "page_context",
    "analyze_scope",
    "scan_overlays",
    "observe_overlays",
    "dom_snapshot",
    "screenshot_element",
    # Typewriter Text Input
    "calculate_typewriter_delay",
    "typewriter_fill",
    "typewriter_type",
    "typewriter_keyboard_type",
]
