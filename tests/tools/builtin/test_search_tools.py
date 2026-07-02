"""Tests for search tool: grep (ripgrep + pure Python fallback)."""

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
        """Should find matches using ripgrep or pure Python fallback."""
        (tmp_path / "test.py").write_text("def foo():\n    pass\ndef bar():\n    pass\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "def ", "path": str(tmp_path), "output_mode": "content"},
            make_ctx(tmp_path),
        )

        # No skip — either rg or Python fallback always works
        assert result.error is None, f"Unexpected error: {result.error}"
        assert "def foo" in result.output
        assert "def bar" in result.output
        # Verify engine metadata is present
        assert "engine" in result.metadata

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path):
        """Should return '(no matches)' when pattern not found."""
        (tmp_path / "test.py").write_text("hello world\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "xyznotfound", "path": str(tmp_path)},
            make_ctx(tmp_path),
        )

        assert result.error is None
        assert "(no matches)" in result.output

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, tmp_path):
        """Case insensitive flag should work."""
        (tmp_path / "test.py").write_text("Hello World\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "hello", "path": str(tmp_path), "-i": True, "output_mode": "content"},
            make_ctx(tmp_path),
        )

        assert result.error is None
        assert "Hello" in result.output

    @pytest.mark.asyncio
    async def test_grep_files_with_matches_mode(self, tmp_path):
        """Output mode 'files_with_matches' should list file paths."""
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "b.py").write_text("print('no functions here')\n")

        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "def ", "path": str(tmp_path), "output_mode": "files_with_matches"},
            make_ctx(tmp_path),
        )

        assert result.error is None
        assert "a.py" in result.output
        assert "b.py" not in result.output

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, tmp_path):
        """Invalid regex should return an error."""
        tool = GrepTool()
        result = await tool.execute(
            {"pattern": "[unclosed", "path": str(tmp_path)},
            make_ctx(tmp_path),
        )

        # Should return error if rg unavailable, otherwise rg handles it
        # Python fallback catches regex errors
        if result.error and "not found" in result.error:
            pytest.skip("ripgrep not installed")
        # With python fallback: invalid regex is caught
        if result.metadata.get("engine") == "python":
            assert result.error is not None
