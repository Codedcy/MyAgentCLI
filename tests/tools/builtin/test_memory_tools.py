"""Tests for memory tool: memory_write."""

import logging

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

    @pytest.mark.asyncio
    async def test_unexpected_write_error_logs_structured_error(self, tmp_path, caplog):
        class FailingMemoryStore:
            async def write(self, file_path: str, content: str):
                raise RuntimeError("store unavailable")

        ctx = make_ctx(tmp_path)
        ctx.memory_store = FailingMemoryStore()
        caplog.set_level(logging.ERROR, logger="myagent.tools.memory")

        tool = MemoryWriteTool()
        result = await tool.execute(
            {
                "file_path": str(tmp_path / "memory" / "fact.md"),
                "content": "---\nname: fact\n---\n\nbody",
            },
            ctx,
        )

        assert result.error is not None
        record = next(record for record in caplog.records if record.name == "myagent.tools.memory")
        assert record.category == "error"
        assert record.component == "tool"
        assert record.context == "memory_write"
        assert record.exc_info is not None
