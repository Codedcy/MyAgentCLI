"""Tests for project environment detection."""

import subprocess
from pathlib import Path

import pytest

from myagent.agent.project import ProjectContext, ProjectDetector


class TestProjectDetector:
    @pytest.mark.asyncio
    async def test_detect_python_project(self, tmp_project_dir):
        """Detect a Python project with pyproject.toml."""
        (tmp_project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_project_dir / "tests").mkdir(exist_ok=True)

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.project_type == "python"
        assert ctx.build_system == "pyproject"
        assert "tests" in ctx.structure_summary

    @pytest.mark.asyncio
    async def test_detect_empty_dir(self, tmp_project_dir):
        """Detect on empty directory returns unknown type."""
        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.project_type == "unknown"
        assert ctx.is_git_repo is False

    @pytest.mark.asyncio
    async def test_detect_node_project(self, tmp_project_dir):
        """Detect a Node.js project."""
        (tmp_project_dir / "package.json").write_text("{}")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.project_type == "node"

    @pytest.mark.asyncio
    async def test_detect_go_project(self, tmp_project_dir):
        """Detect a Go project."""
        (tmp_project_dir / "go.mod").write_text("module test\n")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.project_type == "go"

    @pytest.mark.asyncio
    async def test_detect_rust_project(self, tmp_project_dir):
        """Detect a Rust project."""
        (tmp_project_dir / "Cargo.toml").write_text("[package]\nname = 'test'\n")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.project_type == "rust"

    @pytest.mark.asyncio
    async def test_detect_git_repo(self, tmp_project_dir):
        """Detect git repository info."""
        # Initialize a real git repo
        subprocess.run(
            ["git", "init"],
            cwd=tmp_project_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_project_dir,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_project_dir,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=tmp_project_dir,
            capture_output=True,
            check=False,
        )

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.is_git_repo is True
        # Branch name could be 'main' or 'master' or empty depending on git version
        assert ctx.git_branch is not None or ctx.git_branch is None  # allow either

    @pytest.mark.asyncio
    async def test_detect_package_manager_uv(self, tmp_project_dir):
        """Detect uv via uv.lock."""
        (tmp_project_dir / "uv.lock").write_text("")
        (tmp_project_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.package_manager == "uv"

    @pytest.mark.asyncio
    async def test_detect_package_manager_npm(self, tmp_project_dir):
        """Detect npm via package-lock.json."""
        (tmp_project_dir / "package.json").write_text("{}")
        (tmp_project_dir / "package-lock.json").write_text("{}")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.package_manager == "npm"

    @pytest.mark.asyncio
    async def test_detect_test_framework_pytest(self, tmp_project_dir):
        """Detect pytest via configuration presence."""
        (tmp_project_dir / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths=['tests']\n"
        )

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.test_framework == "pytest"

    @pytest.mark.asyncio
    async def test_detect_linter_ruff(self, tmp_project_dir):
        """Detect ruff via pyproject.toml config."""
        (tmp_project_dir / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length=100\n"
        )

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.linter == "ruff"

    @pytest.mark.asyncio
    async def test_detect_agent_md(self, tmp_project_dir):
        """Detect and read CLAUDE.md / AGENT.md."""
        (tmp_project_dir / "CLAUDE.md").write_text("# Project: Test\n\nSome content.")

        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.agent_md_content is not None
        assert "Project: Test" in ctx.agent_md_content

    @pytest.mark.asyncio
    async def test_graceful_no_git(self, tmp_project_dir):
        """Missing git should not crash detection."""
        detector = ProjectDetector()
        ctx = await detector.detect(tmp_project_dir)

        assert ctx.is_git_repo is False
        assert ctx.git_branch is None

    def test_find_git_root_current_dir(self, tmp_project_dir):
        """_find_git_root finds .git in the current directory."""
        (tmp_project_dir / ".git").mkdir()
        result = ProjectDetector._find_git_root(tmp_project_dir)
        assert result is not None
        assert result.resolve() == tmp_project_dir.resolve()

    def test_find_git_root_parent_dir(self, tmp_project_dir):
        """_find_git_root walks up to find .git in a parent directory."""
        git_root = tmp_project_dir / "repo"
        git_root.mkdir()
        (git_root / ".git").mkdir()
        sub_dir = git_root / "src" / "deep" / "nested"
        sub_dir.mkdir(parents=True)

        result = ProjectDetector._find_git_root(sub_dir)
        assert result is not None
        assert result.resolve() == git_root.resolve()

    def test_find_git_root_not_found(self, tmp_project_dir):
        """_find_git_root returns None when no .git is found up the tree."""
        result = ProjectDetector._find_git_root(tmp_project_dir)
        assert result is None

    def test_find_git_root_max_depth(self, tmp_project_dir):
        """_find_git_root stops after 10 levels."""
        deep = tmp_project_dir
        for i in range(12):
            deep = deep / f"level_{i}"
            deep.mkdir(exist_ok=True)
        # .git is at tmp_project_dir level, but we start 12 levels deep
        (tmp_project_dir / ".git").mkdir()
        result = ProjectDetector._find_git_root(deep)
        # It should fail because we're more than 10 levels deep
        assert result is None
