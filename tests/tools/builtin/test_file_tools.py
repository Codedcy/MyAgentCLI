"""Tests for file tools: read, write, edit, glob."""

from pathlib import Path

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.file_tools import EditTool, GlobTool, ReadTool, WriteTool


def make_ctx(tmp_path):
    return ToolContext(
        session_id="test",
        project_dir=tmp_path,
        permissions=None,
        config=None,
        working_dir=tmp_path,
    )


class TestReadTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        tool = ReadTool()
        result = await tool.execute({"file_path": str(f)}, make_ctx(tmp_path))
        assert result.error is None
        assert "line1" in result.output

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tmp_path):
        tool = ReadTool()
        result = await tool.execute({"file_path": str(tmp_path / "nope.txt")}, make_ctx(tmp_path))
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        tool = ReadTool()
        result = await tool.execute({"file_path": str(f), "offset": 1}, make_ctx(tmp_path))
        assert "line2" in result.output
        assert "line1" not in result.output

    @pytest.mark.asyncio
    async def test_read_with_limit(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")

        tool = ReadTool()
        result = await tool.execute({"file_path": str(f), "offset": 0, "limit": 2}, make_ctx(tmp_path))
        assert "line1" in result.output
        assert "line2" in result.output
        assert "line3" not in result.output


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        f = tmp_path / "new.txt"
        tool = WriteTool()
        result = await tool.execute({"file_path": str(f), "content": "hello"}, make_ctx(tmp_path))
        assert result.error is None
        assert f.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_overwrite(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old")
        tool = WriteTool()
        result = await tool.execute({"file_path": str(f), "content": "new"}, make_ctx(tmp_path))
        assert result.error is None
        assert f.read_text() == "new"


class TestEditTool:
    @pytest.mark.asyncio
    async def test_edit_single_replacement(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("x = 1\ny = 2\n")
        tool = EditTool()
        result = await tool.execute(
            {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 42"},
            make_ctx(tmp_path),
        )
        assert result.error is None
        assert f.read_text() == "x = 42\ny = 2\n"

    @pytest.mark.asyncio
    async def test_edit_not_found(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("hello")
        tool = EditTool()
        result = await tool.execute(
            {"file_path": str(f), "old_string": "not_there", "new_string": "x"},
            make_ctx(tmp_path),
        )
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_edit_duplicate_without_replace_all(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("dup\ndup\n")
        tool = EditTool()
        result = await tool.execute(
            {"file_path": str(f), "old_string": "dup", "new_string": "fixed"},
            make_ctx(tmp_path),
        )
        assert result.error is not None  # should warn about duplicates

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("dup\ndup\n")
        tool = EditTool()
        result = await tool.execute(
            {"file_path": str(f), "old_string": "dup", "new_string": "fixed", "replace_all": True},
            make_ctx(tmp_path),
        )
        assert result.error is None
        assert f.read_text() == "fixed\nfixed\n"


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_glob_matches(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")

        tool = GlobTool()
        result = await tool.execute({"pattern": "*.py", "path": str(tmp_path)}, make_ctx(tmp_path))
        assert result.error is None
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tmp_path):
        tool = GlobTool()
        result = await tool.execute({"pattern": "*.rs", "path": str(tmp_path)}, make_ctx(tmp_path))
        assert result.error is None
        assert "(no matches)" in result.output
