"""Tests for MemoryStore."""

import pytest

from myagent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(
        project_memory_dir=tmp_path / "project" / ".myagent" / "memory",
        user_memory_dir=tmp_path / "home" / ".myagent" / "memory",
    )


CONTENT = """---
name: coding-style
description: Project coding conventions
metadata:
  type: project
---

Use snake_case for all Python code.
"""


class TestMemoryStore:
    @pytest.mark.asyncio
    async def test_write_new(self, store):
        mf = await store.write(str(store.project_dir / "coding-style.md"), CONTENT)
        assert mf.name == "coding-style"
        assert mf.description == "Project coding conventions"

    @pytest.mark.asyncio
    async def test_write_then_read(self, store):
        path = str(store.project_dir / "test.md")
        await store.write(path, CONTENT)
        mf = await store.read("coding-style")
        assert mf is not None
        assert "snake_case" in mf.content

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, store):
        mf = await store.read("nonexistent")
        assert mf is None

    @pytest.mark.asyncio
    async def test_delete(self, store):
        path = str(store.project_dir / "test.md")
        await store.write(path, CONTENT)
        await store.delete("coding-style")
        mf = await store.read("coding-style")
        assert mf is None

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.write(str(store.project_dir / "a.md"), CONTENT)
        entries = await store.list_all("project")
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_session_log(self, store):
        await store.write(str(store.project_dir / "test.md"), CONTENT)
        log = store.get_session_writes()
        assert "coding-style" in log.created
