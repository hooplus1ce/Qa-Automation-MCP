"""vtable_playwright package root.

Provides a fully backward-compatible public interface matching the former
monolithic module while cleanly decomposing the underlying implementation
into high-cohesion, low-coupling submodules:

- config: Central constants, environment flags, and embedded assets.
- mouse: Virtual mouse cursor visualization and smooth 60fps trajectory driver.
- browser: Browser process lifecycle, CDP connection, session management, and page registry.
- overlay: Ant Design Portal and ARIA overlay observation engine.
- vtable: VTable instance binding, scenegraph analysis, cell actions, and data operations.
- interaction: Unified semantic DOM action pipeline, locators, and ARIA snapshots.
"""

from __future__ import annotations

from .config import (
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
    SCROLL_WAIT_RAF,
    SETTLE_MS,
    VTABLE_SHOW_CURSOR,
    VTABLE_VERIFICATION_STRATEGY,
    _CURSOR_HEIGHT,
    _CURSOR_HOT_X,
    _CURSOR_HOT_Y,
    _CURSOR_WIDTH,
    _EMBEDDED_CURSOR_DATA_URL,
    _EMBEDDED_CURSOR_PNG_BASE64,
    _env_bool,
    _env_int,
    active_profile,
)
from .mouse import (
    _WIN_CURSOR_HELPER_SCRIPT,
    _build_cursor_helper_script,
    _ensure_cursor_helper,
    _last_mouse_point,
    _reset_last_mouse_point,
    _smooth_mouse_move_to,
)
from .browser import (
    _action_lock,
    _browser,
    _browser_session_impl,
    _cdp,
    _chrome_executable,
    _chrome_port,
    _chrome_process,
    _chrome_profile,
    _chrome_profile_owned,
    _close_browser_impl,
    _connect_browser_impl,
    _context_id,
    _context_id_counter,
    _context_ids,
    _context_names,
    _current_page_impl,
    _fallback_context_ids,
    _fallback_frame_counters,
    _fallback_frame_ids,
    _frame_context_details,
    _frame_details,
    _frame_id,
    _frame_name_url,
    _frame_page_offset,
    _launch_chrome_impl,
    _list_pages_impl,
    _open_url_impl,
    _owned_contexts,
    _page_frame_counters,
    _page_frame_ids,
    _page_id,
    _page_id_counter,
    _page_viewport_size,
    _pw,
    _select_page_impl,
    _select_page_object,
    _selected_context,
    _selected_page,
    _session_summary,
    _start_browser_impl,
    _wait_for_cdp,
    browser_session,
    close_browser,
    connect_browser,
    current_page,
    launch_chrome,
    list_pages,
    open_url,
    select_page,
    start_browser,
)
from .overlay.scripts import (
    _OVERLAY_ARM_INIT_SCRIPT,
    _OVERLAY_DRAIN_TEMPLATE,
    _OVERLAY_OBSERVER_TEMPLATE,
    _overlay_arm_init_script,
    _overlay_script,
)
from .overlay.listener import (
    _OverlayFrameListener,
    _acquire_overlay_frame_listener,
    _overlay_frame_listeners,
    _release_overlay_frame_listener,
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
from .vtable.binding import (
    _cell_visible_js,
    _resolve_vtable_cell_impl,
    _vtable_directory,
    _wrap,
    _wrap2,
    active_application_frame,
    cell_center,
    cell_offset,
    cell_visible,
    ensure_cell_visible,
    ensure_vtable,
    resolve_frame,
    resolve_vtable_cell,
    vtable_frame,
)
from .vtable.verification import (
    _CELL_CANVAS_SLICE_JS,
    _cell_screenshot,
    _cell_visual_state,
    _verify_landed,
)
from .vtable.analysis import (
    _analysis_cache,
    _analysis_counter,
    _analysis_geometry,
    _analysis_layout_signature,
    _remember_analysis,
    _vtable_analysis_impl,
    vtable_analysis,
)
from .vtable import (
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
from .interaction import (
    _click_dom_impl,
    _dom_interact_impl,
    _perform_dom_action,
    click_dom,
    dom_interact,
)

__all__ = [
    # Config & Profiles
    "ACTIVE_IFRAME_SELECTOR",
    "ACTIVE_PROFILE",
    "ANTD_OVERLAY_SELECTOR",
    "LOCATOR_STRATEGY",
    "NAV_TIMEOUT_MS",
    "OVERLAY_OBSERVER_KEY",
    "OVERLAY_RESULT_LIMIT",
    "VTABLE_SHOW_CURSOR",
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
    # DOM Interaction
    "click_dom",
    "click_dom_and_observe",
    "dom_interact",
    # Scope & Overlays
    "page_context",
    "analyze_scope",
    "scan_overlays",
    "observe_overlays",
    "dom_snapshot",
    "screenshot_element",
]
