"""Tests for CLI main entry and argument parsing."""

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
