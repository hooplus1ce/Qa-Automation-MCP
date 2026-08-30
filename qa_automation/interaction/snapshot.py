"""Aria snapshotting, scoped control analysis, and element screenshotting."""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from ..browser import (
    _action_lock,
    _current_page_impl,
    _frame_context_details,
    _frame_details,
    _frame_id,
    _frame_page_offset,
    _page_id,
)
from ..components.vtable.binding import (
    active_application_frame,
    resolve_frame,
)
from ..config import ACTIVE_PROFILE
from ..workspace import artifact_file
from .locator import _find_interaction_locator

_COMPACT_CONTROL_SCAN = r"""
({scopeSelector, maxResults, customControlSelector}) => {
  const trim = (value, limit = 120) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const escape = value => {
    try { return CSS.escape(String(value)); }
    catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&'); }
  };
  const resolveScope = selector => {
    if (!selector) return document.body;
    let node = document.querySelector(selector);
    if (!node) return null;
    if (node.matches('.ant-modal-root, .ant-drawer-root')) {
      node = node.querySelector('.ant-modal[role="document"], .ant-drawer-content-wrapper, .ant-drawer') || node;
    }
    return node;
  };
  const scopeRoot = resolveScope(scopeSelector);
  if (!scopeRoot) return { controls: [], truncated: false, messages: ['scope-root-not-found'] };

  const isVisible = el => {
    if (!el || el.nodeType !== 1) return false;
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
      if (cur.getAttribute('aria-hidden') === 'true' || cur.hidden) return false;
      const style = window.getComputedStyle(cur);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) <= 0) return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const nameFor = el => {
    const aria = trim(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder'));
    if (aria) return aria;
    const tagged = el.closest ? el.closest('[data-label], [data-title]') : null;
    if (tagged) {
      const labelAttr = trim(tagged.getAttribute('data-label') || tagged.getAttribute('data-title'));
      if (labelAttr) return labelAttr;
    }
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const labelEl = document.getElementById(labelledBy);
      const text = trim(labelEl ? (labelEl.innerText || labelEl.textContent) : '');
      if (text) return text;
    }
    if (el.labels && el.labels.length) {
      const text = trim(Array.from(el.labels).map(item => item.innerText || item.textContent).join(' '));
      if (text) return text;
    }
    const item = el.closest ? el.closest('.ant-form-item, .ant-row, .legions-pro-quick-filter-row') : null;
    if (item) {
      const label = item.querySelector('label, .ant-form-item-label');
      const text = trim(label ? (label.innerText || label.textContent) : '');
      if (text) return text;
    }
    return trim(el.innerText || el.textContent);
  };

  const selectorFor = (el, root) => {
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const value = el.getAttribute(attr);
      if (value) return `[${attr}="${escape(value)}"]`;
    }
    if (el.id) return `#${escape(el.id)}`;
    const tag = el.tagName.toLowerCase();
    const classes = Array.from(el.classList || [])
      .filter(item => !['ant-btn-clicked', 'active', 'focus', 'hover', 'selected'].includes(item))
      .slice(0, 3)
      .map(item => `.${escape(item)}`)
      .join('');
    const base = `${tag}${classes}`;
    const scopeDoc = root || document;
    const sameBase = Array.from(scopeDoc.querySelectorAll(base));
    if (sameBase.length === 1) return base;

    let path = [];
    for (let cur = el; cur && cur !== root && cur !== document.body && path.length < 3; cur = cur.parentElement) {
      const curTag = cur.tagName.toLowerCase();
      const curClasses = Array.from(cur.classList || []).slice(0, 2).map(item => `.${escape(item)}`).join('');
      const parent = cur.parentElement;
      const index = parent ? Array.from(parent.children).indexOf(cur) + 1 : 1;
      path.unshift(`${curTag}${curClasses}:nth-of-type(${index})`);
    }
    const candidate = path.join(' > ');
    if (scopeDoc.querySelectorAll(candidate).length === 1) return candidate;
    const index = sameBase.indexOf(el);
    return index >= 0 ? `${base} >> nth=${index}` : base;
  };

  const baseQuery = [
    'button',
    'input',
    'textarea',
    'select',
    'a[href]',
    '[role="button"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="combobox"]',
    '[role="textbox"]',
    '[role="tab"]',
    '.ant-select-selection',
    '.ant-pagination-item',
    '.ant-pagination-prev',
    '.ant-pagination-next',
  ].join(',');
  const query = customControlSelector ? `${baseQuery},${customControlSelector}` : baseQuery;
  const candidates = Array.from(scopeRoot.querySelectorAll(query));
  const controls = [];
  let truncated = false;

  for (const el of candidates) {
    if (el.closest('.vtable, .vtable-canvas, canvas')) continue;
    if (!isVisible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const explicitRole = el.getAttribute('role');
    const role = explicitRole || (
      tag === 'button' ? 'button' :
      tag === 'select' ? 'combobox' :
      tag === 'textarea' ? 'textbox' :
      tag === 'input' ? (
        ['checkbox', 'radio'].includes(el.type) ? el.type :
        ['button', 'submit', 'reset'].includes(el.type) ? 'button' : 'textbox'
      ) :
      el.classList.contains('ant-select-selection') ? 'combobox' : 'control'
    );
    const rect = el.getBoundingClientRect();
    controls.push({
      role,
      name: nameFor(el),
      description: trim(el.getAttribute('aria-description') || el.getAttribute('title')) || null,
      css: selectorFor(el, scopeRoot === document.body ? null : scopeRoot),
      tag,
      input_type: el.getAttribute('type') || '',
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('ant-btn-disabled')),
      readonly: Boolean(el.readOnly || el.getAttribute('aria-readonly') === 'true'),
      state: {
        ...(role === 'checkbox' || role === 'radio' ? { checked: Boolean(el.checked || el.getAttribute('aria-checked') === 'true') } : {}),
        ...(role === 'combobox' ? { expanded: Boolean(el.getAttribute('aria-expanded') === 'true' || el.closest('.ant-select-open')) } : {}),
      },
      box: {
        x: Math.round(rect.x * 100) / 100,
        y: Math.round(rect.y * 100) / 100,
        width: Math.round(rect.width * 100) / 100,
        height: Math.round(rect.height * 100) / 100,
      },
    });
    if (controls.length >= maxResults) {
      truncated = true;
      break;
    }
  }
  return { controls, truncated, messages: [] };
}
"""


async def _focused_editable(page: Page) -> dict[str, Any] | None:
    for frame in page.frames:
        try:
            item = await frame.evaluate(
                """() => {
                  const el = document.activeElement;
                  if (!el || el === document.body) return null;
                  const tag = el.tagName.toLowerCase();
                  const editable = el.isContentEditable || ['input', 'textarea'].includes(tag);
                  if (!editable) return null;
                  return {
                    tag,
                    role: el.getAttribute('role') || (tag === 'textarea' ? 'textbox' : el.type || 'textbox'),
                    name: el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('placeholder') || null,
                    selector: el.id ? `#${CSS.escape(el.id)}` : (el.name ? `${tag}[name="${CSS.escape(el.name)}"]` : tag),
                    value: el.value !== undefined ? String(el.value).slice(0, 300) : (el.innerText || '').slice(0, 300),
                  };
                }"""
            )
            if item:
                return item
        except Exception:
            continue
    return None


async def _dom_snapshot_impl(
    *,
    selector: str | None = None,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
) -> dict:
    page = await _current_page_impl()
    target_frame = await resolve_frame(page, frame)
    target = target_frame.locator(selector) if selector else target_frame.locator(":root")
    kwargs: dict[str, Any] = {"mode": "ai" if ai_mode else "default", "boxes": bool(boxes)}
    if depth is not None and depth > 0:
        kwargs["depth"] = depth
    snapshot = await target.aria_snapshot(**kwargs)
    return {
        "status": "ok",
        "selector": selector,
        "frame": frame,
        "mode": kwargs["mode"],
        "boxes": kwargs["boxes"],
        "snapshot": snapshot,
    }


async def dom_snapshot(
    selector: str | None = None,
    *,
    frame: str | None = None,
    depth: int | None = None,
    boxes: bool = True,
    ai_mode: bool = True,
) -> dict:
    async with _action_lock:
        return await _dom_snapshot_impl(
            selector=selector,
            frame=frame,
            depth=depth,
            boxes=boxes,
            ai_mode=ai_mode,
        )


async def _analyze_scope_impl(
    *,
    max_controls: int = 40,
    max_overlays: int = 10,
) -> dict:
    from ..overlay import _scan_overlays_impl
    page = await _current_page_impl()
    active_frame = await active_application_frame(page)
    overlays_resp = await _scan_overlays_impl(max_results=max_overlays, scope="active")
    focus = (overlays_resp.get("context") or {}).get("focus_layer")

    target_frame = active_frame or page.main_frame
    scope_selector = None
    mode = "active_application"
    if focus is not None:
        target_frame = await resolve_frame(page, focus.get("frame_name") or focus.get("frame_id"))
        scope_selector = focus.get("selector")
        mode = "focus_layer"

    scan_result = await target_frame.evaluate(
        _COMPACT_CONTROL_SCAN,
        {
            "scopeSelector": scope_selector,
            "maxResults": max_controls,
            "customControlSelector": ACTIVE_PROFILE.custom_control_selector,
        },
    )

    offset = await _frame_page_offset(page, target_frame)
    controls = []
    for idx, c in enumerate(scan_result.get("controls") or []):
        b = c["box"]
        controls.append(
            {
                "role": c["role"],
                "name": c["name"],
                "description": c["description"],
                "css": c["css"],
                "tag": c["tag"],
                "input_type": c["input_type"],
                "disabled": c["disabled"],
                "readonly": c["readonly"],
                "state": c["state"],
                "ref": f"c{idx+1}",
                "frame": "active" if target_frame == active_frame else "top",
                "frame_id": _frame_id(page, target_frame),
                "scope": mode,
                "page_box": {
                    "x": round(b["x"] + offset["x"], 2),
                    "y": round(b["y"] + offset["y"], 2),
                    "width": b["width"],
                    "height": b["height"],
                },
            }
        )

    title = await page.title()
    url = page.url
    breadcrumb: list[str] = []
    active_tab: str | None = None
    try:
        breadcrumb = await page.evaluate(
            """() => Array.from(
                 document.querySelectorAll(
                   '.ant-breadcrumb li, .ant-breadcrumb span, '
                   '[class*="breadcrumb"] a, [class*="breadcrumb"] span'
                 )
               )
               .map(el => (el.innerText || '').trim())
               .filter(Boolean)
               .slice(0, 8)"""
        )
    except Exception:
        pass
    try:
        active_tab = await page.evaluate(
            """() => {
              const el = document.querySelector(
                '.ant-tabs-tab-active, .ant-tabs-tab .ant-tabs-tab-btn-active'
              );
              return el ? (el.innerText || '').trim() || null : null;
            }"""
        )
    except Exception:
        pass
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page": {
            "page_id": _page_id(page),
            "url": url,
            "title": title,
            "breadcrumb": breadcrumb,
            "active_tab": active_tab,
        },
        "scope": {
            "mode": mode,
            "kind": focus.get("kind") if focus else None,
            "selector": scope_selector,
            "frame": _frame_details(page, target_frame),
        } if focus else {
            "mode": mode,
            "active_iframe": await _frame_context_details(page, active_frame) if active_frame else None,
        },
        "focus_layer": focus,
        "messages": scan_result.get("messages") or [],
        "controls": controls,
        "control_count": len(controls),
        "truncated": bool(scan_result.get("truncated")),
        "errors": [],
    }


async def analyze_scope(
    *, max_controls: int = 40, max_overlays: int = 10
) -> dict:
    async with _action_lock:
        return await _analyze_scope_impl(
            max_controls=max_controls, max_overlays=max_overlays
        )


async def _screenshot_element_impl(
    *,
    role: str | None = None,
    name: str | None = None,
    description: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    css: str | None = None,
    xpath: str | None = None,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    frame: str | None = None,
    in_iframe: bool = True,
    padding: float = 0,
    image_format: str = "png",
    filename: str | None = None,
    quality: int | None = None,
    timeout_ms: float = 3_000,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    if image_format not in {"png", "jpeg"}:
        raise ValueError("image_format must be 'png' or 'jpeg'")
    if padding < 0 or padding > 200:
        raise ValueError("padding must be between 0 and 200 CSS pixels")
    if max_bytes < 1_024 or max_bytes > 20_000_000:
        raise ValueError("max_bytes must be between 1024 and 20000000")

    page = await _current_page_impl()
    locator = target_frame = locator_source = None
    locator_supplied = any([role, text, placeholder, css, xpath])
    coordinate_supplied = any(value is not None for value in (x, y, width, height))
    if coordinate_supplied and not all(value is not None for value in (x, y, width, height)):
        raise ValueError("viewport screenshot requires x, y, width and height")
    if locator_supplied:
        locator, target_frame, locator_source = await _find_interaction_locator(
            page,
            role=role,
            name=name,
            description=description,
            text=text,
            placeholder=placeholder,
            css=css,
            xpath=xpath,
            frame=frame,
            in_iframe=in_iframe,
            timeout_ms=timeout_ms,
        )
    elif not coordinate_supplied:
        raise ValueError("one locator or x/y/width/height viewport rectangle is required")
    elif frame not in {None, "top", "main", "main_frame"}:
        raise ValueError("viewport screenshot coordinates are top-page CSS pixels; omit frame")

    if locator is not None:
        target = locator.first
        await target.wait_for(state="visible", timeout=timeout_ms)
        box = await target.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise ValueError("target element has no visible bounding box")
        clip = {
            "x": max(0.0, float(box["x"]) - padding),
            "y": max(0.0, float(box["y"]) - padding),
            "width": float(box["width"]) + padding * 2,
            "height": float(box["height"]) + padding * 2,
        }
        frame_details = _frame_details(page, target_frame)
    else:
        clip = {
            "x": max(0.0, float(x)),
            "y": max(0.0, float(y)),
            "width": float(width),
            "height": float(height),
        }
        if clip["width"] <= 0 or clip["height"] <= 0:
            raise ValueError("viewport screenshot width and height must be positive")
        frame_details = _frame_details(page, page.main_frame)

    screenshot_kwargs: dict[str, Any] = {"clip": clip, "type": image_format}
    if image_format == "jpeg" and quality is not None:
        if not 1 <= quality <= 100:
            raise ValueError("quality must be between 1 and 100")
        screenshot_kwargs["quality"] = quality
    image = await page.screenshot(**screenshot_kwargs)
    expected_extensions = {".png"} if image_format == "png" else {".jpg", ".jpeg"}
    requested_filename = filename
    if requested_filename:
        suffix = "." + requested_filename.rsplit(".", 1)[-1].lower() if "." in requested_filename else ""
        if suffix and suffix not in expected_extensions:
            raise ValueError(
                f"filename extension must match image_format={image_format!r}"
            )
        if not suffix:
            requested_filename = f"{requested_filename}.{image_format}"
    output_path = artifact_file(
        "screenshots",
        requested_filename,
        fallback=f"screenshot.{image_format}",
        unique=filename is None,
    )
    output_path.write_bytes(image)
    if len(image) > max_bytes:
        return {
            "status": "failed",
            "reason": "screenshot-too-large",
            "byte_size": len(image),
            "max_bytes": max_bytes,
            "clip": {key: round(value, 2) for key, value in clip.items()},
            "path": str(output_path),
        }
    return {
        "status": "ok",
        "image_base64": base64.b64encode(image).decode("ascii"),
        "mime_type": f"image/{image_format}",
        "byte_size": len(image),
        "digest": hashlib.sha256(image).hexdigest()[:16],
        "path": str(output_path),
        "clip": {key: round(value, 2) for key, value in clip.items()},
        "page_id": _page_id(page),
        "frame": frame_details,
        "locator": {
            "resolved_by": locator_source,
            "css": css,
            "role": role,
            "name": name,
        } if locator_source else None,
    }


async def screenshot_element(**kwargs: Any) -> dict[str, Any]:
    async with _action_lock:
        return await _screenshot_element_impl(**kwargs)

async def _page_context_impl(*, max_results: int = 10) -> dict:
    from ..overlay import _scan_overlays_impl
    page = await _current_page_impl()
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    active = await active_application_frame(page)
    frames = list(page.frames)
    frame_items = []
    for frame in frames[: max(1, int(max_results))]:
        detail = await _frame_context_details(page, frame)
        detail["scope"] = (
            "active_iframe"
            if active is not None and frame == active
            else "top_document"
            if frame == page.main_frame
            else "iframe"
        )
        frame_items.append(detail)
    overlays = await _scan_overlays_impl(max_results=max_results, scope="active")
    return {
        "status": "ok",
        "profile": ACTIVE_PROFILE.name,
        "page_id": _page_id(page),
        "url": page.url,
        "title": title,
        "frame_count": len(frames),
        "active_iframe": (
            await _frame_context_details(page, active) if active is not None else None
        ),
        "frames": frame_items,
        "focus_layer": overlays.get("context", {}).get("focus_layer"),
        "visible_overlays": overlays.get("overlays", []),
        "observer_errors": overlays.get("observer_errors", []),
    }


async def page_context(*, max_results: int = 10) -> dict:
    async with _action_lock:
        return await _page_context_impl(max_results=max_results)
