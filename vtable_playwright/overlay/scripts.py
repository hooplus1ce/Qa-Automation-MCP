"""JavaScript templates for Ant Design Portal and overlay mutation observation."""

from __future__ import annotations

import json
from ..config import (
    ANTD_OVERLAY_SELECTOR,
    OVERLAY_EVENT_LIMIT,
    OVERLAY_OBSERVER_KEY,
)

_OVERLAY_OBSERVER_TEMPLATE = r"""
(() => {
  const key = __KEY__;
  const selector = __SELECTOR__;
  const maxEvents = __MAX_EVENTS__;
  const reset = __RESET__;

  const trimText = (value, limit = 1000) => String(value || "")
    .replace(/\s+/g, " ").trim().slice(0, limit);
  const isElement = node => node && node.nodeType === 1;
  const isVisible = el => {
    if (!isElement(el)) return false;
    for (let current = el; isElement(current); current = current.parentElement) {
      if (current.getAttribute("aria-hidden") === "true" || current.hidden) return false;
      const style = getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) <= 0) return false;
      if (String(current.className || "").toLowerCase().split(/\s+/).some(token =>
        /(?:dropdown|modal|mask|drawer|popover|tooltip|message|notification).*hidden$/.test(token) ||
        token === "hidden" || token === "--hidden"
      )) return false;
    }
    const rect = el.getBoundingClientRect();
    if (Number(getComputedStyle(el).zIndex) < 0) return false;
    return rect.width > 0 && rect.height > 0;
  };
  const classText = el => String(el.className || "").toLowerCase();
  const kindFor = el => {
    const cls = classText(el);
    const role = String(el.getAttribute("role") || "").toLowerCase();
    if (role === "dialog" || role === "alertdialog" || /modal|dialog/.test(cls)) return "dialog";
    if (/drawer/.test(cls)) return "drawer";
    if (role === "listbox" || role === "menu" || /dropdown|select|picker|cascader|tree-select|mentions|menu|vtable.*popup|filter-menu|virtual-option/.test(cls) || el.querySelector?.(".virtual-option")) return "dropdown";
    if (role === "alert" || role === "status" || el.hasAttribute("aria-live") || /message|notification|alert/.test(cls)) return "notification";
    if (/popover|popconfirm|tooltip|tour|bubble-tooltip/.test(cls)) return "popover";
    return "overlay";
  };
  const cssEscape = value => {
    try { return CSS.escape(String(value)); } catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); }
  };
  const selectorFor = el => {
    for (const attr of ["data-testid", "data-test", "data-qa", "data-cy"]) {
      const value = el.getAttribute(attr);
      if (value) return `[${attr}="${cssEscape(value)}"]`;
    }
    if (el.id) return `#${cssEscape(el.id)}`;
    const stateClasses = new Set([
      "active", "focus", "hover", "checked", "selected", "disabled", "loading", "animating", "hidden",
      "move-up-enter", "move-up-enter-active", "move-up-leave", "move-up-leave-active",
      "zoom-appear", "zoom-appear-active", "zoom-leave", "zoom-leave-active", "zoom-enter", "zoom-enter-active",
      "fade-enter", "fade-enter-active", "fade-leave", "fade-leave-active",
      "ant-zoom-appear", "ant-zoom-appear-active", "ant-zoom-enter", "ant-zoom-leave",
    ]);
    const classes = classText(el).split(/\s+/).filter(Boolean)
      .filter(value => !stateClasses.has(value) && !/(?:-enter|-leave|-appear|zoom-|move-up|fade-)/.test(value)).slice(0, 4);
    const role = el.getAttribute("role");
    const own = `${el.tagName.toLowerCase()}${classes.map(value => `.${cssEscape(value)}`).join("")}${role ? `[role="${cssEscape(role)}"]` : ""}`;
    if (document.querySelectorAll(own).length === 1) return own;
    let current = el;
    const parts = [];
    for (let depth = 0; current && depth < 3; depth++, current = current.parentElement) {
      const part = current.id ? `#${cssEscape(current.id)}` : `${current.tagName.toLowerCase()}${classText(current).split(/\s+/).filter(Boolean).filter(value => !stateClasses.has(value)).slice(0, 2).map(value => `.${cssEscape(value)}`).join("")}`;
      parts.unshift(part || current.tagName.toLowerCase());
      const candidate = parts.join(" > ");
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const matches = Array.from(document.querySelectorAll(own));
    const index = matches.indexOf(el);
    return index > 0 ? `${own} >> nth=${index}` : own;
  };
  const canonicalOverlay = element => {
    if (!isElement(element)) return null;
    if (element.matches(".virtual-option")) {
      const parent = element.parentElement;
      return parent && parent.querySelectorAll(".virtual-option").length > 1 ? parent : element;
    }
    if (element.matches && element.matches(".ant-modal-root, .ant-modal-wrap, .ant-drawer-root")) {
      const inner = element.querySelector(".ant-modal, .ant-drawer-content-wrapper, .ant-drawer");
      if (inner) return inner;
    }
    if (element.matches && element.matches(".ant-modal-content")) {
      const dialog = element.closest(".ant-modal");
      if (dialog) return dialog;
    }
    return element;
  };
  const describe = (el, event) => {
    if (!isElement(el)) return null;
    const visible = isVisible(el);
    if (!visible && event !== "added" && event !== "removed") return null;
    const rect = el.getBoundingClientRect();
    const role = el.getAttribute("role") || null;
    const options = el.querySelectorAll ? Array.from(el.querySelectorAll(".virtual-option")) : [];
    const text = trimText(options.length > 1
      ? options.slice(0, 3).map(option => option.innerText || option.textContent).join(" | ")
      : (el.innerText || el.textContent), 320);
    const label = trimText(el.getAttribute("aria-label") || el.getAttribute("title"), 300) || null;
    const kind = kindFor(el);
    const selectorText = selectorFor(el);
    const identity = [kind, selectorText, role || "", label || ""].join("|");
    const result = {
      event: event || "visible",
      kind,
      tag: el.tagName.toLowerCase(),
      role,
      selector: selectorText,
      class_name: String(el.className || "").slice(0, 500),
      text,
      label,
      identity,
      fingerprint: `${identity}|${text}`,
      visible,
      box: visible ? {
        x: Math.round(rect.x * 100) / 100,
        y: Math.round(rect.y * 100) / 100,
        width: Math.round(rect.width * 100) / 100,
        height: Math.round(rect.height * 100) / 100,
      } : null,
      timestamp: Date.now(),
    };
    if (options.length > 1) {
      result.option_count = options.length;
      result.option_preview = options.slice(0, 3).map(option => trimText(option.innerText || option.textContent, 100));
    }
    return result;
  };
  const candidateRoots = (node, includeDescendants = false, event = "") => {
    let root = node;
    if (!isElement(root)) root = root && root.parentElement;
    if (!isElement(root)) return [];
    if (event === "removed" && root.matches(".virtual-option") && !root.parentElement) return [];
    const roots = [];
    if (root.matches(selector)) roots.push(canonicalOverlay(root));
    else {
      const closest = root.closest(selector);
      if (closest) roots.push(canonicalOverlay(closest));
    }
    if (includeDescendants) {
      for (const child of root.querySelectorAll(selector)) {
        const canonicalChild = canonicalOverlay(child);
        if (!canonicalChild) continue;
        if (!roots.some(parent => parent === canonicalChild || parent.contains(canonicalChild) || canonicalChild.contains(parent))) roots.push(canonicalChild);
      }
    }
    return roots;
  };
  const reused = !!(window[key] && window[key].version === 1);
  const state = reused
    ? window[key]
    : { version: 1, events: [], seen: {}, seenOrder: [], selector, observer: null, collect: null, baseline: [] };
  if (!state.seenOrder) state.seenOrder = [];
  if (!state.collect) {
    state.collect = () => {
      const roots = [];
      for (const candidate of document.querySelectorAll(selector)) {
        const root = canonicalOverlay(candidate);
        if (!root || roots.some(parent => parent === root || parent.contains(root))) continue;
        roots.push(root);
      }
      return roots.map(el => describe(el, "visible")).filter(Boolean);
    };
  }
  if (reset && state.observer) {
    state.observer.takeRecords();
    state.observer.disconnect();
    state.observer = null;
  }
  if (reset || !reused) {
    state.events = [];
    state.seen = {};
    state.seenOrder = [];
    state.baseline = state.collect();
  }
  if (!state.observer) {
    state.record = (node, event, includeDescendants = false) => {
      for (const root of candidateRoots(node, includeDescendants, event)) {
        const item = describe(root, event);
        if (!item) continue;
        const dedupeKey = `${item.event}|${item.fingerprint}`;
        const previous = state.seen[dedupeKey];
        if (previous && item.timestamp - previous < 40) continue;
        state.seen[dedupeKey] = item.timestamp;
        state.seenOrder.push(dedupeKey);
        state.events.push(item);
        if (state.events.length > maxEvents) state.events.splice(0, state.events.length - maxEvents);
        while (state.seenOrder.length > maxEvents * 4) {
          const staleKey = state.seenOrder.shift();
          if (staleKey) delete state.seen[staleKey];
        }
      }
    };
    state.observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        if (mutation.type === "attributes") state.record(mutation.target, "changed");
        else for (const node of mutation.addedNodes) state.record(node, "added", true);
        for (const node of mutation.removedNodes) state.record(node, "removed", true);
        if (mutation.type === "characterData") state.record(mutation.target, "changed");
      }
    });
    state.observer.observe(document.documentElement || document, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
      attributeFilter: [
        "class", "style", "hidden", "aria-hidden", "aria-live", "role", "open",
        "data-state", "data-open", "data-visible", "aria-expanded", "aria-haspopup",
      ],
    });
    window[key] = state;
  }
  return { installed: true, reused, baseline: state.baseline || state.collect() };
})()
"""

_OVERLAY_DRAIN_TEMPLATE = r"""
(() => {
  const key = __KEY__;
  const state = window[key];
  if (!state || !state.collect) return { events: [], current: [], baseline: [], installed: false };
  const current = state.collect();
  const result = {
    events: state.events.splice(0), current, baseline: state.baseline || [], installed: true,
  };
  if (__STOP__) {
    if (state.observer) state.observer.disconnect();
    delete window[key];
  } else {
    state.baseline = current;
  }
  return result;
})()
"""


def _overlay_script(template: str, *, stop: bool = False, reset: bool = False) -> str:
    return (
        template.replace("__KEY__", json.dumps(OVERLAY_OBSERVER_KEY))
        .replace("__SELECTOR__", json.dumps(ANTD_OVERLAY_SELECTOR))
        .replace("__MAX_EVENTS__", str(OVERLAY_EVENT_LIMIT))
        .replace("__STOP__", "true" if stop else "false")
        .replace("__RESET__", "true" if reset else "false")
    )


def _overlay_arm_init_script(deadline_ms: float) -> str:
    observer = _overlay_script(_OVERLAY_OBSERVER_TEMPLATE, reset=False)
    key = json.dumps(OVERLAY_OBSERVER_KEY)
    return f"""(() => {{
  const deadline = {int(deadline_ms)};
  if (Date.now() >= deadline) return null;
  const key = {key};
  const install = () => {{
    try {{
      if (window[key] && window[key].observer) return;
      {observer}
    }} catch (_) {{}}
  }};
  install();
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", install, {{ once: true }});
  }}
}})()"""

_OVERLAY_ARM_INIT_SCRIPT = _overlay_arm_init_script
