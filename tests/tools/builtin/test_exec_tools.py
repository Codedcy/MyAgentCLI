"""Tests for exec tool: bash."""

from pathlib import Path

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.exec_tools import BashTool


def make_ctx(tmp_path):
    return ToolContext(
        session_id="test",
        project_dir=tmp_path,
        permissions=None,
        config=None,
        working_dir=tmp_path,
    )


class TestBashTool:
    @pytest.mark.asyncio
    async def test_bash_echo(self, tmp_path):
        tool = BashTool()
        result = await tool.execute({"command": "echo hello world"}, make_ctx(tmp_path))
        assert result.error is None
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_bash_nonexistent_command(self, tmp_path):
        tool = BashTool()
        result = await tool.execute({"command": "nonexistent_command_xyz 2>&1"}, make_ctx(tmp_path))
        # Should not crash, may have error output
        assert result is not None
