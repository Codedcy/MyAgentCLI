"""Tests for exec tool: bash."""

import logging

import pytest

import myagent.tools.builtin.exec_tools as exec_tools
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
    async def test_windows_uses_real_bash_when_available(self, tmp_path, monkeypatch):
        captured: dict = {}

        class FakeProcess:
            pid = 123
            returncode = 0

            async def communicate(self):
                return b"done\n", b""

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(exec_tools.sys, "platform", "win32")
        monkeypatch.setattr(
            exec_tools,
            "_resolve_bash_executable",
            lambda: r"C:\Program Files\Git\bin\bash.exe",
        )
        monkeypatch.setattr(
            exec_tools.asyncio,
            "create_subprocess_exec",
            fake_create_subprocess_exec,
        )

        tool = BashTool()
        result = await tool.execute(
            {"command": "mkdir -p app && echo done"},
            make_ctx(tmp_path),
        )

        assert result.error is None
        assert result.output == "done"
        assert captured["args"] == (
            r"C:\Program Files\Git\bin\bash.exe",
            "-lc",
            "mkdir -p app && echo done",
        )
        assert captured["kwargs"]["cwd"] == str(tmp_path)
        assert result.metadata["shell"] == "bash"

    @pytest.mark.asyncio
    async def test_windows_rejects_posix_command_when_bash_is_missing(
        self,
        tmp_path,
        monkeypatch,
    ):
        shell_called = False

        async def fake_create_subprocess_shell(*args, **kwargs):
            nonlocal shell_called
            shell_called = True
            raise AssertionError("native shell must not receive POSIX mkdir -p")

        monkeypatch.setattr(exec_tools.sys, "platform", "win32")
        monkeypatch.setattr(exec_tools, "_resolve_bash_executable", lambda: None)
        monkeypatch.setattr(
            exec_tools.asyncio,
            "create_subprocess_shell",
            fake_create_subprocess_shell,
        )

        tool = BashTool()
        result = await tool.execute(
            {"command": "mkdir -p app && echo Done > app/status.txt"},
            make_ctx(tmp_path),
        )

        assert shell_called is False
        assert result.error is not None
        assert "Bash executable not found" in result.error
        assert not (tmp_path / "-p").exists()
        assert not (tmp_path / "mkdir").exists()
        assert not (tmp_path / "Done").exists()

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
        monkeypatch.setattr(
            "myagent.tools.builtin.exec_tools.asyncio.create_subprocess_exec",
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
