"""Tests for web tools: web_fetch, web_search."""

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.web_tools import WebFetchTool, WebSearchTool


def make_ctx(tmp_path):
    return ToolContext(
        session_id="test",
        project_dir=tmp_path,
        permissions=None,
        config=None,
        working_dir=tmp_path,
    )


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_web_search_basic(self, tmp_path):
        tool = WebSearchTool()
        result = await tool.execute({"query": "Python asyncio"}, make_ctx(tmp_path))
        assert result.error is None
        assert result.output  # should return placeholder result
