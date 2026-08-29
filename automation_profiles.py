"""Small, explicit UI profiles and interaction strategies."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LocatorStrategy:
    order: tuple[str, ...] = (
        "css",
        "ax-role",
        "xpath",
        "text",
        "placeholder",
        "coordinate",
    )


@dataclass(frozen=True)
class VTableVerificationStrategy:
    order: tuple[str, ...] = (
        "selection-changed",
        "target-selected",
        "editor-open",
        "scenegraph-changed",
        "screenshot-changed",
    )
    retry_count: int = 1
    screenshot_size: int = 14


@dataclass(frozen=True)
class PageProfile:
    name: str
    active_iframe_selector: str
    overlay_selectors: tuple[str, ...]
    custom_control_selector: str
    dropdown_selectors: tuple[str, ...]
    dropdown_option_selector: str


COMMON_OVERLAY_SELECTORS = (
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[aria-modal="true"]',
    '[role="alert"]',
    '[role="status"]',
    '[role="listbox"]',
    '[aria-live="polite"]',
    '[aria-live="assertive"]',
    ".ant-modal-root",
    ".ant-modal-wrap",
    ".ant-modal",
    ".ant-drawer-root",
    ".ant-drawer-content-wrapper",
    ".ant-drawer",
    ".ant-dropdown",
    ".ant-dropdown-menu",
    ".ant-select-dropdown",
    ".ant-picker-dropdown",
    ".ant-cascader-dropdown",
    ".ant-tree-select-dropdown",
    ".ant-mentions-dropdown",
    ".ant-popover",
    ".ant-popconfirm",
    ".ant-tooltip",
    ".ant-message",
    ".ant-message-notice",
    ".ant-notification",
    ".ant-notification-notice",
    ".ant-notification-notice-wrapper",
    ".ant-tour",
    ".ant-image-preview-root",
    ".vtable-filter-menu",
    ".vtable__menu-element",
    ".vtable__bubble-tooltip-element",
    ".vtable__dropdown",
    ".vtable__popup",
    ".virtual-option",
)

ANTD_CONTROL_SELECTOR = (
    ".ant-select,.ant-cascader,.ant-tree-select,.ant-picker,.ant-switch,"
    ".ant-btn,.ant-checkbox-wrapper,.ant-radio-wrapper"
)

PROFILES = {
    "aps-antd": PageProfile(
        name="aps-antd",
        active_iframe_selector=(
            '.ant-tabs-tabpane[role="tabpanel"][aria-hidden="false"] iframe'
        ),
        overlay_selectors=COMMON_OVERLAY_SELECTORS,
        custom_control_selector=ANTD_CONTROL_SELECTOR,
        dropdown_selectors=(
            ".ant-select-dropdown:not(.ant-select-dropdown-hidden)",
            ".ant-cascader-dropdown:not(.ant-cascader-dropdown-hidden)",
            ".ant-cascader-menus",
            ".ant-tree-select-dropdown:not(.ant-select-dropdown-hidden)",
        ),
        dropdown_option_selector=(
            ".ant-select-item-option, .ant-select-dropdown-menu-item, "
            ".ant-select-tree-treenode, .ant-cascader-menu-item, .virtual-option"
        ),
    ),
}

LOCATOR_STRATEGY = LocatorStrategy()
VTABLE_VERIFICATION_STRATEGY = VTableVerificationStrategy()


def active_profile() -> PageProfile:
    name = os.getenv("UI_AUTOMATION_PROFILE", "aps-antd").strip().lower()
    profile = PROFILES.get(name)
    if profile is None:
        raise ValueError(
            f"unknown UI_AUTOMATION_PROFILE {name!r}; choose one of {sorted(PROFILES)}"
        )
    iframe_override = os.getenv("UI_ACTIVE_IFRAME_SELECTOR") or os.getenv(
        "VTABLE_ACTIVE_IFRAME_SELECTOR"
    )
    if iframe_override:
        return PageProfile(**{**asdict(profile), "active_iframe_selector": iframe_override})
    return profile


def profile_contract() -> dict:
    profile = active_profile()
    return {
        "profile": asdict(profile),
        "locator_strategy": asdict(LOCATOR_STRATEGY),
        "vtable_verification_strategy": asdict(VTABLE_VERIFICATION_STRATEGY),
    }
