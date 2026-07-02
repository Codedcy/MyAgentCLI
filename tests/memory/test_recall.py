"""Tests for memory recall."""

import pytest

from myagent.memory.recall import recall
from myagent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(
        project_memory_dir=tmp_path / "project" / ".myagent" / "memory",
        user_memory_dir=tmp_path / "home" / ".myagent" / "memory",
    )


class TestRecall:
    @pytest.mark.asyncio
    async def test_recall_finds_relevant(self, store):
        content = """---
name: python-style
description: Python coding style rules
metadata:
  type: project
---

Use snake_case. Prefer type annotations.
"""
        await store.write(str(store.project_dir / "python-style.md"), content)

        results = await recall("python coding", store)
        assert len(results) >= 1
        assert results[0].name == "python-style"

    @pytest.mark.asyncio
    async def test_recall_empty_for_no_match(self, store):
        results = await recall("nonexistent topic xyz", store)
        assert len(results) == 0
