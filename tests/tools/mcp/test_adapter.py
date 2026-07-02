"""Tests for MCP tool adapter (schema translation + execute)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.mcp.adapter import MCPToolAdapter


class TestMCPToolAdapter:
    def test_schema_translation_basic(self):
        """Adapter should translate MCP inputSchema to OpenAI format."""
        client = AsyncMock()
        raw_tool = {
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        }

        adapter = MCPToolAdapter(raw_tool, client)

        assert adapter.name == "read_file"
        assert adapter.description == "Read a file"
        assert adapter.parameters["type"] == "object"
        assert "path" in adapter.parameters["properties"]
        assert "required" in adapter.parameters
        assert adapter.parameters["required"] == ["path"]

    def test_schema_translation_empty(self):
        """Empty inputSchema should return minimal params."""
        client = AsyncMock()
        raw_tool = {
            "name": "noop",
            "inputSchema": {},
        }

        adapter = MCPToolAdapter(raw_tool, client)
        assert adapter.parameters == {"type": "object", "properties": {}}

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """execute should call MCP client and return ToolResult."""
        client = AsyncMock()
        client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "Hello world"}]
        })

        raw_tool = {"name": "echo", "inputSchema": {}}
        adapter = MCPToolAdapter(raw_tool, client)

        ctx = ToolContext(
            session_id="test-session",
            project_dir=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
        )

        result = await adapter.execute({"message": "Hello"}, ctx)

        assert result.error is None
        assert result.output == "Hello world"
        client.call_tool.assert_called_once_with("echo", {"message": "Hello"})

    @pytest.mark.asyncio
    async def test_execute_error(self):
        """execute errors should be captured in ToolResult.error."""
        client = AsyncMock()
        client.call_tool = AsyncMock(side_effect=RuntimeError("MCP server crashed"))

        raw_tool = {"name": "bad_tool", "inputSchema": {}}
        adapter = MCPToolAdapter(raw_tool, client)

        ctx = ToolContext(
            session_id="test-session",
            project_dir=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
        )

        result = await adapter.execute({}, ctx)
        assert result.output == ""
        assert result.error == "MCP server crashed"
