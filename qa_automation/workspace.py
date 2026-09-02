"""Workspace-scoped paths for generated artifacts and local inputs."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def workspace_root() -> Path:
    """Return the project root currently using this MCP service."""
    current = Path.cwd().resolve()
    # 统一使用 QA_AUTOMATION_PROJECT_ROOT（支持绝对路径或相对路径，兼容历史别名），默认回退到当前工作目录 .
    configured = (
        os.getenv("QA_AUTOMATION_PROJECT_ROOT", "").strip()
        or os.getenv("QA_AUTOMATION_PROJECT_DIR", "").strip()
        or os.getenv("QA_AUTOMATION_WORKSPACE_ROOT", "").strip()
        or "."
    )
    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = current / root
    root = root.resolve()
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    if not root.is_dir():
        raise ValueError(f"MCP consumer project root is not a directory: {root}")
    return root


def resolve_workspace_path(
    value: str | os.PathLike[str],
    *,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path:
    """Resolve a path inside the MCP consumer project and reject escapes."""
    root = workspace_root()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(
            f"Path must stay inside MCP consumer workspace {root}: {path}"
        )
    if must_exist and not path.exists():
        raise ValueError(f"Workspace path does not exist: {path}")
    if require_file and not path.is_file():
        raise ValueError(f"Workspace path is not a file: {path}")
    return path


def artifact_root() -> Path:
    """Return and create the workspace-local artifact root."""
    root = resolve_workspace_path(
        os.getenv("QA_AUTOMATION_ARTIFACT_ROOT", ".qa-automation")
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_dir(category: str) -> Path:
    """Return and create one direct child of the artifact root."""
    if not category or Path(category).name != category or category in {".", ".."}:
        raise ValueError(f"Invalid artifact category: {category!r}")
    path = artifact_root() / category
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """Return and create the configured runtime data directory."""
    configured = os.getenv("QA_AUTOMATION_DATA_DIR")
    path = resolve_workspace_path(configured) if configured else artifact_dir("data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(value: str | None, *, fallback: str) -> str:
    """Convert an untrusted browser filename into one safe Windows basename."""
    name = Path((value or "").replace("\\", "/")).name
    name = _INVALID_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        name = fallback
    if Path(name).stem.upper() in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    return name[:180]


def artifact_file(
    category: str,
    filename: str | None,
    *,
    fallback: str,
    unique: bool = False,
) -> Path:
    """Allocate a safe file path in an artifact category."""
    directory = artifact_dir(category)
    name = safe_filename(filename, fallback=fallback)
    path = directory / name
    if unique or path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    return path
