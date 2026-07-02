"""Tests for ContextBuilder."""

from unittest.mock import MagicMock

import pytest

from myagent.agent.project import ProjectContext
from myagent.context.builder import ContextBuilder


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
