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
        # gap-8-11: When DuckDuckGo API is unavailable, the tool returns
        # an error result (not a misleading stub). In CI/local without
        # network, this is the expected behavior.
        if result.error is not None:
            assert "search_error" in result.metadata
            assert "DuckDuckGo" in result.error.lower() or "unavailable" in result.error.lower()
        else:
            assert result.output  # API available — should return real results
