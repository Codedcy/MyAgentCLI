"""Tests for exec tool: bash."""

import logging
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
    def test_bash_schema_does_not_expose_permission_bypass(self):
        tool = BashTool()
        assert "dangerouslyDisableSandbox" not in tool.parameters["properties"]

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

    @pytest.mark.asyncio
    async def test_bash_unexpected_exception_logs_error_metadata(
        self, tmp_path, monkeypatch, caplog
    ):
        async def fail_create_subprocess_shell(*args, **kwargs):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr(
            "myagent.tools.builtin.exec_tools.asyncio.create_subprocess_shell",
            fail_create_subprocess_shell,
        )

        tool = BashTool()
        with caplog.at_level(logging.ERROR, logger="myagent.tools.exec"):
            result = await tool.execute({"command": "echo nope"}, make_ctx(tmp_path))

        assert result.error == "spawn failed"
        error_records = [
            r for r in caplog.records
            if getattr(r, "category", None) == "error"
        ]
        assert error_records
        assert error_records[0].component == "tool"
        assert error_records[0].context == "bash.execute"
