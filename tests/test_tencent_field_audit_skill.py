"""Tests for tencent-sheet-style-augmentation skill and scanner tools in FastMCP."""

from __future__ import annotations

import pytest

from qa_automation.mcp.server import mcp


@pytest.mark.asyncio
async def test_fastmcp_registers_tencent_sheet_style_skills():
    """Verify that FastMCP's SkillsDirectoryProvider discovers the style augmentation skill."""
    resources = await mcp.list_resources()
    resource_uris = [str(r.uri) for r in resources]

    assert "skill://tencent-sheet-style-augmentation/SKILL.md" in resource_uris
    assert "skill://tencent-sheet-style-augmentation/_manifest" in resource_uris

    # Read the skill resource content
    content = await mcp.read_resource("skill://tencent-sheet-style-augmentation/SKILL.md")
    assert content is not None
    assert "Tencent Sheet Style Augmentation" in str(content)
    assert "cellDataGrid" in str(content)
    assert "Full Style Matrix" in str(content)


@pytest.mark.asyncio
async def test_fastmcp_registers_scan_styles_tools():
    """Verify that FastMCP registers the scan tools."""
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]

    assert "tencent_sheet_scan_styles" in tool_names
    assert "tencent_sheet_scan_deleted_fields" in tool_names

    tool = next(t for t in tools if t.name == "tencent_sheet_scan_styles")
    assert "全维度提取" in tool.description
