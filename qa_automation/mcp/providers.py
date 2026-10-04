"""FastMCP providers owned by the application composition root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp.apps.approval import Approval
from fastmcp.apps.choice import Choice
from fastmcp.apps.file_upload import FileUpload
from fastmcp.apps.generative import GenerativeUI
from fastmcp.server.providers import FileSystemProvider
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from qa_automation.mcp.apps.provider import create_app


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_generative_doc() -> None:
    """Keep prefab UI examples compatible with prompt-variable parsers."""
    try:
        import prefab_ui.generative as _gen_ui

        doc = _gen_ui.execute.__doc__
        if doc and "{{" in doc:
            _gen_ui.execute.__doc__ = doc.replace("{{", "{").replace("}}", "}")
    except Exception:
        # The optional provider is allowed to remain unavailable in minimal runs.
        pass


def build_providers(
    *,
    include_demos: bool,
    include_prefab_ui: bool,
) -> list[Any]:
    """Build providers in one place so the root server stays declarative.

    ``FileSystemProvider`` follows FastMCP's component discovery convention for
    static resources. Domain servers that share browser state remain explicit
    mounted servers under ``mcp.servers``.
    """
    components_root = Path(__file__).resolve().parent / "components"
    skills_root = Path(__file__).resolve().parents[2] / "skills"
    reload_components = _env_bool("QA_AUTOMATION_FASTMCP_RELOAD")

    providers: list[Any] = [
        Approval(title="确认执行该测试用例?"),
        Choice(),
        FileUpload(),
        FileSystemProvider(root=components_root, reload=reload_components),
        SkillsDirectoryProvider(roots=skills_root, reload=reload_components),
    ]
    if include_demos:
        providers.insert(0, create_app())
    if include_prefab_ui:
        _sanitize_generative_doc()
        providers.append(GenerativeUI())
    return providers


__all__ = ["build_providers"]
