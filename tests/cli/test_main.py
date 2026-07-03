"""Tests for CLI main entry and argument parsing."""

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

cli_main = importlib.import_module("myagent.cli.main")
from myagent.cli.main import parse_args


class TestArgParsing:
    def test_defaults(self):
        args = parse_args([])
        assert args.resume is None
        assert args.list_sessions is False
        assert args.mode is None

    def test_mode(self):
        args = parse_args(["--mode", "think-max"])
        assert args.mode == "think-max"

    def test_goal(self):
        args = parse_args(["--goal", "Implement feature X"])
        assert args.goal == "Implement feature X"

    def test_list_sessions(self):
        args = parse_args(["--list-sessions"])
        assert args.list_sessions is True

    def test_resume_without_id(self):
        args = parse_args(["--resume"])
        assert args.resume == "__latest__"

    def test_resume_with_id(self):
        args = parse_args(["--resume", "2026-07-03-abc123"])
        assert args.resume == "2026-07-03-abc123"

    def test_dangerously_skip(self):
        args = parse_args(["--dangerously-skip-permissions"])
        assert args.dangerously_skip_permissions is True

    def test_project_dir(self):
        args = parse_args(["--project-dir", "D:/work/project"])
        assert args.project_dir == "D:/work/project"


@pytest.mark.asyncio
async def test_startup_mcp_servers_uses_project_dir(tmp_path, monkeypatch):
    registry = MagicMock()
    project_dir = tmp_path / "project"
    mcp_dir = project_dir / ".myagent"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "mcp.json").write_text(
        json.dumps({"servers": {"project-server": {"command": "project-cmd"}}}),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli_main.Path, "home", classmethod(lambda cls: home))

    start_mock = AsyncMock(return_value=object())
    monkeypatch.setattr(cli_main, "_start_single_mcp_server", start_mock)

    clients = await cli_main._startup_mcp_servers(registry, project_dir)

    assert len(clients) == 1
    start_mock.assert_awaited_once_with(
        "project-server", {"command": "project-cmd"}, registry
    )


@pytest.mark.asyncio
async def test_startup_mcp_servers_project_overrides_user_server(
    tmp_path, monkeypatch
):
    registry = MagicMock()
    home = tmp_path / "home"
    user_mcp = home / ".myagent"
    user_mcp.mkdir(parents=True)
    (user_mcp / "mcp.json").write_text(
        json.dumps({"servers": {"same": {"command": "user-cmd"}}}),
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    project_mcp = project_dir / ".myagent"
    project_mcp.mkdir(parents=True)
    (project_mcp / "mcp.json").write_text(
        json.dumps({"servers": {"same": {"command": "project-cmd"}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_main.Path, "home", classmethod(lambda cls: home))

    start_mock = AsyncMock(return_value=object())
    monkeypatch.setattr(cli_main, "_start_single_mcp_server", start_mock)

    await cli_main._startup_mcp_servers(registry, project_dir)

    start_mock.assert_awaited_once_with(
        "same", {"command": "project-cmd"}, registry
    )
