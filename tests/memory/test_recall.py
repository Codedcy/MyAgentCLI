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

    @pytest.mark.asyncio
    async def test_recall_matches_chinese_project_memory_without_spaces(
        self, store, monkeypatch
    ):
        import importlib

        recall_module = importlib.import_module("myagent.memory.recall")
        monkeypatch.setattr(recall_module, "_get_embedding_model", lambda: None)

        role_rule = (
            "\u5f53\u7528\u6237\u63d0\u5230\u4ee5\u4e0b\u89d2\u8272\u65f6\uff0c"
            "\u4f7f\u7528 `spawn_subagent` "
            "\u521b\u5efa\u5bf9\u5e94\u7684\u5b50\u4ee3\u7406\u6267\u884c\u4efb\u52a1\u3002"
        )
        content = "\n".join([
            "---",
            'title: "\u5f00\u53d1\u56e2\u961f\u914d\u7f6e"',
            "members:",
            '  - id: "pm"',
            '    role: "\u4ea7\u54c1\u7ecf\u7406"',
            "---",
            "",
            "# \u5f00\u53d1\u56e2\u961f",
            "",
            role_rule,
            "",
        ])
        await store.write(str(store.project_dir / "dev-team.md"), content)

        results = await recall("\u6709\u56e2\u961f\u53ef\u7528\u4e48", store)

        assert [result.name for result in results] == ["dev-team"]
