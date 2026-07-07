"""Tests for ContextBuilder."""

from unittest.mock import MagicMock

import pytest

from myagent.agent.project import ProjectContext
from myagent.context.builder import ContextBuilder
from myagent.memory.store import MemoryStore


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_build_includes_all_layers(self):
        tool_reg = MagicMock()
        tool_reg.get_schemas = MagicMock(return_value=[])
        mem_store = MagicMock()
        skill_reg = MagicMock()
        skill_reg.list_all = MagicMock(return_value=[])

        builder = ContextBuilder(tool_reg, mem_store, skill_reg)
        ctx = ProjectContext(project_type="python", structure_summary="src/ tests/")

        request = await builder.build("Hello", [], ctx)

        assert "MyAgent" in request.system
        assert "python" in request.system
        assert request.messages[-1]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_build_with_tool_subset(self):
        tool_reg = MagicMock()
        tool_reg.get_schemas_for = MagicMock(return_value=[
            {"type": "function", "function": {"name": "read", "description": "", "parameters": {}}}
        ])
        tool_reg.get_schemas = MagicMock()

        builder = ContextBuilder(tool_reg, MagicMock(), MagicMock())
        request = await builder.build("test", [], ProjectContext(), tool_subset=["read"])

        assert len(request.tools) == 1
        tool_reg.get_schemas_for.assert_called_once()
        tool_reg.get_schemas.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_includes_recalled_memory_content_for_new_session(
        self, tmp_path, monkeypatch
    ):
        import importlib

        recall_module = importlib.import_module("myagent.memory.recall")
        monkeypatch.setattr(recall_module, "_get_embedding_model", lambda: None)

        tool_reg = MagicMock()
        tool_reg.get_schemas = MagicMock(return_value=[])
        skill_reg = MagicMock()
        skill_reg.list_all = MagicMock(return_value=[])
        memory_store = MemoryStore(
            project_memory_dir=tmp_path / "project" / ".myagent" / "memory",
            user_memory_dir=tmp_path / "home" / ".myagent" / "memory",
        )
        role_rule = (
            "\u5f53\u7528\u6237\u63d0\u5230\u4ee5\u4e0b\u89d2\u8272\u65f6\uff0c"
            "\u4f7f\u7528 `spawn_subagent` "
            "\u521b\u5efa\u5bf9\u5e94\u7684\u5b50\u4ee3\u7406\u6267\u884c\u4efb\u52a1\u3002"
        )
        pm_rule = (
            "- **\u4ea7\u54c1\u7ecf\u7406**: "
            "\u7528 PRD/\u7528\u6237\u6545\u4e8b\u683c\u5f0f\u8f93\u51fa\u3002"
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
            pm_rule,
            "",
        ])
        await memory_store.write(str(memory_store.project_dir / "dev-team.md"), content)

        builder = ContextBuilder(tool_reg, memory_store, skill_reg)
        request = await builder.build(
            "\u6709\u56e2\u961f\u53ef\u7528\u4e48",
            [],
            ProjectContext(project_type="python"),
        )

        assert "## Relevant Memories" in request.system
        assert "dev-team" in request.system
        assert "`spawn_subagent`" in request.system
        assert "\u4ea7\u54c1\u7ecf\u7406" in request.system
