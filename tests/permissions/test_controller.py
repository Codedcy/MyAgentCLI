"""Tests for PermissionController."""

import pytest

from myagent.permissions.controller import (
    AutoAllowConfig,
    AutoDenyConfig,
    PermissionController,
    PermissionResult,
)


class TestPermissionController:
    def test_level_0_auto_allowed_by_default(self):
        pc = PermissionController()
        result = pc.check("read")
        assert result == PermissionResult.ALLOW

    def test_level_1_asks_by_default(self):
        pc = PermissionController()
        result = pc.check("write")
        assert result == PermissionResult.ASK

    def test_level_2_asks_by_default(self):
        pc = PermissionController()
        result = pc.check("bash")
        assert result == PermissionResult.ASK

    def test_auto_deny_blocks(self):
        pc = PermissionController(
            auto_deny=AutoDenyConfig(paths=["*.env"], commands=[])
        )
        result = pc.check("read", params={"file_path": "/etc/.env"})
        assert result == PermissionResult.DENY

    def test_auto_allow_overrides_ask(self):
        pc = PermissionController(
            auto_allow=AutoAllowConfig(levels=[0, 1], paths=[], commands=[])
        )
        result = pc.check("write")
        assert result == PermissionResult.ALLOW

    def test_deny_takes_priority_over_allow(self):
        pc = PermissionController(
            auto_allow=AutoAllowConfig(levels=[0, 1], paths=["*"], commands=[]),
            auto_deny=AutoDenyConfig(paths=["*.env"], commands=[]),
        )
        result = pc.check("write", params={"file_path": "/secret.env"})
        assert result == PermissionResult.DENY

    def test_allow_all_mode(self):
        pc = PermissionController(default_mode="allow_all")
        result = pc.check("bash")
        assert result == PermissionResult.ALLOW

    def test_skip_all_bypasses_everything(self):
        pc = PermissionController(
            auto_deny=AutoDenyConfig(paths=["*"], commands=["*"])
        )
        pc.skip_all(True)
        result = pc.check("bash", params={"command": "rm -rf /"})
        assert result == PermissionResult.ALLOW

    def test_set_mode_runtime(self):
        pc = PermissionController()
        assert pc.check("bash") == PermissionResult.ASK
        pc.set_mode("allow_all")
        assert pc.check("bash") == PermissionResult.ALLOW

    def test_command_pattern_matching(self):
        pc = PermissionController(
            auto_allow=AutoAllowConfig(levels=[], paths=[], commands=["git *"])
        )
        result = pc.check("bash", params={"command": "git status"})
        assert result == PermissionResult.ALLOW

    def test_command_pattern_no_match(self):
        pc = PermissionController(
            auto_allow=AutoAllowConfig(levels=[], paths=[], commands=["git *"]),
            auto_deny=AutoDenyConfig(paths=[], commands=[]),  # clear default denies
        )
        result = pc.check("bash", params={"command": "ls -la"})
        assert result == PermissionResult.ASK

    def test_unknown_tool_defaults_to_level_3(self):
        pc = PermissionController()
        result = pc.check("mcp_external_tool")
        assert result == PermissionResult.ASK

    def test_confirm_returns_true(self):
        pc = PermissionController()
        import asyncio
        result = asyncio.run(pc.confirm("read", {"file_path": "/tmp/test"}))
        assert result is True
