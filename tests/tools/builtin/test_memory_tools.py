"""Tests for memory tool: memory_write."""

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.memory_tools import MemoryWriteTool


def make_ctx(tmp_path):
    return ToolContext(
        session_id="test",
        project_dir=tmp_path,
        permissions=None,
        config=None,
        working_dir=tmp_path,
    )


class TestMemoryWriteTool:
    @pytest.mark.asyncio
    async def test_write_valid_memory(self, tmp_path):
        f = tmp_path / "memory" / "test-fact.md"
        tool = MemoryWriteTool()
        content = """---
name: test-fact
description: A test fact
---

This is a test memory.
"""
        result = await tool.execute(
            {"file_path": str(f), "content": content},
            make_ctx(tmp_path),
        )
        assert result.error is None
        assert f.exists()
        assert "name: test-fact" in f.read_text()

    @pytest.mark.asyncio
    async def test_write_missing_frontmatter(self, tmp_path):
        f = tmp_path / "memory" / "bad.md"
        tool = MemoryWriteTool()
        result = await tool.execute(
            {"file_path": str(f), "content": "No frontmatter here"},
            make_ctx(tmp_path),
        )
        assert result.error is not None
        assert "frontmatter" in result.error.lower()
