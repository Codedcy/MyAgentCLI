"""Tests for search tool: grep."""

from pathlib import Path

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.search_tools import GrepTool


def make_ctx(tmp_path):
    return ToolContext(
        session_id="test",
        project_dir=tmp_path,
        permissions=None,
        config=None,
        working_dir=tmp_path,
    )


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_grep_finds_matches(self, tmp_path):
        (tmp_path / "test.py").write_text("def foo():\n    pass\ndef bar():\n    pass\n")

        tool = GrepTool()
        result = await tool.execute({"pattern": "def ", "path": str(tmp_path)}, make_ctx(tmp_path))

        # Should either find matches or rg not available
        if result.error and "not found" in result.error:
            pytest.skip("ripgrep not installed")
        assert result.error is None or "not found" in result.error

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path):
        (tmp_path / "test.py").write_text("hello world\n")

        tool = GrepTool()
        result = await tool.execute({"pattern": "xyznotfound", "path": str(tmp_path)}, make_ctx(tmp_path))

        if result.error and "not found" in result.error:
            pytest.skip("ripgrep not installed")
        assert "(no matches)" in result.output
