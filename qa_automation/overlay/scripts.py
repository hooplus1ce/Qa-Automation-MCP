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
  const observe = __OBSERVE__;

  const trimText = (value, limit = 1000) => String(value || "")
    .replace(/\s+/g, " ").trim().slice(0, limit);
  const isElement = node => node && node.nodeType === 1;
  const classText = el => String(el.className || "").toLowerCase();
  const stateClasses = new Set([
    "active", "focus", "hover", "checked", "selected", "disabled", "loading", "animating", "hidden",
    "move-up-enter", "move-up-enter-active", "move-up-leave", "move-up-leave-active",
    "zoom-appear", "zoom-appear-active", "zoom-leave", "zoom-leave-active", "zoom-enter", "zoom-enter-active",
    "fade-enter", "fade-enter-active", "fade-leave", "fade-leave-active",
    "ant-zoom-appear", "ant-zoom-appear-active", "ant-zoom-enter", "ant-zoom-leave",
  ]);
  const stableClasses = el => classText(el).split(/\s+/).filter(Boolean)
    .filter(value => !stateClasses.has(value) && !/(?:-enter|-leave|-appear|zoom-|move-up|fade-)/.test(value));
  const cssEscape = value => {
    try { return CSS.escape(String(value)); }
    catch (_) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); }
  };
  const queryCount = value => {
    try { return document.querySelectorAll(value).length; }
    catch (_) { return 0; }
  };
  const selectorFor = el => {
    for (const attr of ["data-testid", "data-test", "data-qa", "data-cy"]) {
      const value = el.getAttribute(attr);
      const candidate = value ? `[${attr}="${cssEscape(value)}"]` : "";
      if (candidate && queryCount(candidate) === 1) return candidate;
    }
    if (el.id) {
      const candidate = `#${cssEscape(el.id)}`;
      if (queryCount(candidate) === 1) return candidate;
    }
    const role = el.getAttribute("role");
    const own = `${el.tagName.toLowerCase()}${stableClasses(el).slice(0, 4)
      .map(value => `.${cssEscape(value)}`).join("")}${role ? `[role="${cssEscape(role)}"]` : ""}`;
    if (queryCount(own) === 1) return own;

    const parts = [];
    for (let current = el; isElement(current); current = current.parentElement) {
      if (current.id) {
        const idSelector = `#${cssEscape(current.id)}`;
        if (queryCount(idSelector) === 1) {
          parts.unshift(idSelector);
          const candidate = parts.join(" > ");
          if (queryCount(candidate) === 1) return candidate;
        }
      }
      const tag = current.tagName.toLowerCase();
      const classes = stableClasses(current).slice(0, 2)
        .map(value => `.${cssEscape(value)}`).join("");
      let part = `${tag}${classes}`;
      const parent = current.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter(child => child.tagName === current.tagName);
        if (sameTag.length > 1) part += `:nth-of-type(${sameTag.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      const candidate = parts.join(" > ");
      if (queryCount(candidate) === 1) return candidate;
    }
    return own;
  };
  const kindFor = el => {
    const cls = classText(el);
    const role = String(el.getAttribute("role") || "").toLowerCase();
    if (role === "dialog" || role === "alertdialog" || /modal|dialog/.test(cls)) return "dialog";
    if (/drawer/.test(cls)) return "drawer";
    if (role === "listbox" || role === "menu" || /dropdown|select|picker|cascader|tree-select|mentions|menu|vtable.*popup|filter-menu|virtual-option/.test(cls) || el.querySelector?.(".virtual-option")) return "dropdown";
    if (role === "alert" || role === "status" || el.hasAttribute("aria-live") || /message|notification|alert/.test(cls)) return "notification";
    if (/tooltip|bubble-tooltip/.test(cls)) return "tooltip";
    if (/popover|popconfirm|tour/.test(cls)) return "popover";
    return "overlay";
  };
  const canonicalOverlay = element => {
    if (!isElement(element)) return null;
    if (element.matches(".virtual-option")) {
      const parent = element.parentElement;
      return parent && parent.querySelectorAll(".virtual-option").length > 1 ? parent : element;
    }
    if (element.matches(".ant-modal-root, .ant-modal-wrap")) {
      return element.querySelector(".ant-modal") || element;
    }
    if (element.matches(".ant-modal-content")) {
      return element.closest(".ant-modal") || element;
    }
    if (element.matches(".ant-drawer-root")) {
      return element.querySelector(".ant-drawer-content-wrapper, .ant-drawer") || element;
    }
    if (element.matches(".ant-notification-notice-wrapper")) {
      return element.querySelector(".ant-notification-notice") || element;
    }
    if (element.matches(".ant-message") && element.querySelector(".ant-message-notice")) return null;
    if (element.matches(".ant-notification") && element.querySelector(".ant-notification-notice")) return null;
    return element;
  };
  const visualState = el => {
    const rect = el.getBoundingClientRect();
    let rendered = rect.width > 0 && rect.height > 0;
    let pointerEnabled = true;
    let left = Math.max(0, rect.left);
    let top = Math.max(0, rect.top);
    let right = Math.min(window.innerWidth, rect.right);
    let bottom = Math.min(window.innerHeight, rect.bottom);
    let zIndex = 0;
    for (let current = el; rendered && isElement(current); current = current.parentElement) {
      if (current.getAttribute("aria-hidden") === "true" || current.hidden ||
          current.hasAttribute("inert")) {
        rendered = false;
        break;
      }
      const style = getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse" || style.contentVisibility === "hidden" ||
          Number(style.opacity || 1) <= 0) {
        rendered = false;
        break;
      }
      if (style.pointerEvents === "none") pointerEnabled = false;
      const zi = Number(style.zIndex);
      if (Number.isFinite(zi)) zIndex = Math.max(zIndex, zi);
      if (current !== el) {
        const clipsX = /hidden|clip|auto|scroll/.test(style.overflowX);
        const clipsY = /hidden|clip|auto|scroll/.test(style.overflowY);
        if (clipsX || clipsY) {
          const ancestorRect = current.getBoundingClientRect();
          if (clipsX) {
            left = Math.max(left, ancestorRect.left);
            right = Math.min(right, ancestorRect.right);
          }
          if (clipsY) {
            top = Math.max(top, ancestorRect.top);
            bottom = Math.min(bottom, ancestorRect.bottom);
          }
        }
      }
    }
    const viewportVisible = rendered && right > left && bottom > top;
    let actionable = viewportVisible && pointerEnabled;
    if (actionable) {
      const insetX = Math.min(2, Math.max(0, (right - left) / 4));
      const insetY = Math.min(2, Math.max(0, (bottom - top) / 4));
      const points = [
        [(left + right) / 2, (top + bottom) / 2],
        [left + insetX, top + insetY],
        [right - insetX, top + insetY],
        [left + insetX, bottom - insetY],
        [right - insetX, bottom - insetY],
      ];
      actionable = points.some(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return Boolean(hit && (hit === el || el.contains(hit)));
      });
    }
    return {rect, rendered, viewportVisible, actionable, zIndex};
  };
  const overlayIdFor = el => {
    const role = el.getAttribute("role") || "";
    return `${kindFor(el)}|${selectorFor(el)}|${role}`;
  };
  const overlayAncestors = el => {
    const found = [];
    const seen = new Set([el]);
    for (let current = el.parentElement; isElement(current); current = current.parentElement) {
      if (!current.matches(selector)) continue;
      const root = canonicalOverlay(current);
      if (!root || seen.has(root)) continue;
      seen.add(root);
      found.push(root);
    }
    return found;
  };
  const stackOrder = el => {
    const seen = new Set();
    let order = 0;
    for (const candidate of document.querySelectorAll(selector)) {
      const root = canonicalOverlay(candidate);
      if (!root || seen.has(root)) continue;
      seen.add(root);
      if (root === el) return order;
      order += 1;
    }
    return -1;
  };
  const describe = (el, event) => {
    if (!isElement(el)) return null;
    const state = visualState(el);
    if (!state.viewportVisible && event !== "added" && event !== "removed") return null;
    const role = el.getAttribute("role") || null;
    const options = el.querySelectorAll ? Array.from(el.querySelectorAll(".virtual-option")) : [];
    const text = trimText(options.length > 1
      ? options.slice(0, 3).map(option => option.innerText || option.textContent).join(" | ")
      : (el.innerText || el.textContent), 320);
    const label = trimText(el.getAttribute("aria-label") || el.getAttribute("title"), 300) || null;
    const kind = kindFor(el);
    const selectorText = selectorFor(el);
    const overlayId = `${kind}|${selectorText}|${role || ""}`;
    const ancestors = overlayAncestors(el);
    const result = {
      event: event || "visible",
      kind,
      tag: el.tagName.toLowerCase(),
      role,
      selector: selectorText,
      class_name: String(el.className || "").slice(0, 500),
      text,
      label,
      overlay_id: overlayId,
      parent_overlay_id: ancestors.length ? overlayIdFor(ancestors[0]) : null,
      identity: overlayId,
      fingerprint: `${overlayId}|${text}`,
      rendered: state.rendered,
      viewport_visible: state.viewportVisible,
      actionable: state.actionable,
      visible: state.viewportVisible,
      contains_focus: el === document.activeElement || el.contains(document.activeElement),
      overlay_depth: ancestors.length,
      z_index: state.zIndex,
      stack_order: stackOrder(el),
      box: state.viewportVisible ? {
        x: Math.round(state.rect.x * 100) / 100,
        y: Math.round(state.rect.y * 100) / 100,
        width: Math.round(state.rect.width * 100) / 100,
        height: Math.round(state.rect.height * 100) / 100,
      } : null,
      timestamp: Date.now(),
    };
    if (!result.parent_overlay_id) delete result.parent_overlay_id;
    if (options.length > 1) {
      result.option_count = options.length;
      result.option_preview = options.slice(0, 3)
        .map(option => trimText(option.innerText || option.textContent, 100));
    }
    return result;
  };
  const candidateRoots = (node, includeDescendants = false, event = "") => {
    let root = node;
    if (!isElement(root)) root = root && root.parentElement;
    if (!isElement(root)) return [];
    if (event === "removed" && root.matches(".virtual-option") && !root.parentElement) return [];
    const roots = [];
    const append = candidate => {
      const canonical = canonicalOverlay(candidate);
      if (canonical && !roots.includes(canonical)) roots.push(canonical);
    };
    if (root.matches(selector)) append(root);
    else {
      const closest = root.closest(selector);
      if (closest) append(closest);
    }
    if (includeDescendants) {
      for (const child of root.querySelectorAll(selector)) append(child);
    }
    return roots;
  };
  const collect = () => {
    const roots = [];
    for (const candidate of document.querySelectorAll(selector)) {
      const root = canonicalOverlay(candidate);
      if (root && !roots.includes(root)) roots.push(root);
    }
    return roots.map(el => describe(el, "visible")).filter(Boolean);
  };

  if (!observe) {
    return {installed: false, reused: false, current: collect(), baseline: []};
  }

  const reused = !!(
    window[key] && window[key].version === 2 && window[key].selector === selector
  );
  const state = reused
    ? window[key]
    : {
        version: 2, events: [], droppedEvents: 0, seen: {}, seenOrder: [],
        selector, observer: null, collect, baseline: [],
      };
  if (!state.seenOrder) state.seenOrder = [];
  if (!Number.isFinite(state.droppedEvents)) state.droppedEvents = 0;
  state.collect = collect;
  if (reset && state.observer) {
    state.observer.takeRecords();
    state.observer.disconnect();
    state.observer = null;
  }
  if (reset || !reused) {
    state.events = [];
    state.droppedEvents = 0;
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
        if (state.events.length > maxEvents) {
          const excess = state.events.length - maxEvents;
          state.events.splice(0, excess);
          state.droppedEvents += excess;
        }
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
        "inert",
      ],
    });
    window[key] = state;
  }
  return {installed: true, reused, baseline: state.baseline || state.collect()};
})()
"""

_OVERLAY_DRAIN_TEMPLATE = r"""
(() => {
  const key = __KEY__;
  const state = window[key];
  if (!state || !state.collect) {
    return {
      events: [], current: [], baseline: [], installed: false,
      events_truncated: false, dropped_event_count: 0,
    };
  }
  const current = state.collect();
  const droppedEventCount = Number(state.droppedEvents || 0);
  const result = {
    events: state.events.splice(0),
    current,
    baseline: state.baseline || [],
    installed: true,
    events_truncated: droppedEventCount > 0,
    dropped_event_count: droppedEventCount,
  };
  state.droppedEvents = 0;
  if (__STOP__) {
    if (state.observer) state.observer.disconnect();
    delete window[key];
  } else {
    state.baseline = current;
  }
  return result;
})()
"""


def _overlay_script(
    template: str,
    *,
    stop: bool = False,
    reset: bool = False,
    observe: bool = True,
) -> str:
    return (
        template.replace("__KEY__", json.dumps(OVERLAY_OBSERVER_KEY))
        .replace("__SELECTOR__", json.dumps(ANTD_OVERLAY_SELECTOR))
        .replace("__MAX_EVENTS__", str(OVERLAY_EVENT_LIMIT))
        .replace("__STOP__", "true" if stop else "false")
        .replace("__RESET__", "true" if reset else "false")
        .replace("__OBSERVE__", "true" if observe else "false")
    )


_OVERLAY_DEADLINE_VAR = "__qa_overlay_deadline_ms"


def _overlay_arm_script() -> str:
    """常驻 arm 脚本:deadline 从 window 变量读取,便于每次交互只刷新变量、
    不再重复 add_init_script(避免长会话下脚本无限累积、每次导航全量重放)。
    动态 iframe 的 window 独立于主文档,deadline 沿 parent 链向上查找。"""
    observer = _overlay_script(_OVERLAY_OBSERVER_TEMPLATE, reset=False)
    key = json.dumps(OVERLAY_OBSERVER_KEY)
    return f"""(() => {{
  const readDeadline = () => {{
    let w = window;
    for (let i = 0; i < 6 && w; i++) {{
      const v = Number(w.{_OVERLAY_DEADLINE_VAR} || 0);
      if (v) return v;
      try {{ if (w === w.parent) break; w = w.parent; }} catch (_) {{ return 0; }}
    }}
    return 0;
  }};
  const deadline = readDeadline();
  if (!deadline || Date.now() >= deadline) return null;
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


_OVERLAY_ARM_INIT_SCRIPT = _overlay_arm_script
