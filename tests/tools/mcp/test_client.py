"""Tests for MCP client (stdio JSON-RPC subprocess)."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from myagent.tools.mcp.client import MCPClient, RawToolDef


def _make_mock_process(stdin_writer=None, stdout_reader=None):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.stdin = AsyncMock()
    proc.stdout = AsyncMock()
    proc.stderr = AsyncMock()
    proc.pid = 12345
    return proc


class TestMCPClient:
    @pytest.mark.asyncio
    async def test_start_and_handshake(self):
        """Client should send initialize request and handle response."""
        client = MCPClient(command="echo", args=["hello"])

        # Mock subprocess creation and response
        with patch.object(
            client, "_send_request", AsyncMock(return_value={"protocolVersion": "2024-11-05"})
        ), patch.object(
            client, "_send_notification", AsyncMock()
        ), patch.object(
            client, "_reader_loop", AsyncMock()
        ):
            # Manually set up process mock
            client._process = _make_mock_process()
            client._reader_task = asyncio.create_task(asyncio.sleep(0))
            client._started = True

            assert client._started is True

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """list_tools should parse MCP tools/list response."""
        client = MCPClient(command="echo", args=["hello"])
        client._started = True
        client._process = _make_mock_process()

        mock_response = {
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
                {
                    "name": "write_file",
                    "description": "Write a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            ]
        }

        with patch.object(client, "_send_request", AsyncMock(return_value=mock_response)):
            tools = await client.list_tools()

        assert len(tools) == 2
        assert tools[0].name == "read_file"
        assert tools[0].description == "Read a file"
        assert tools[1].name == "write_file"
        assert "path" in tools[0].inputSchema["properties"]

    @pytest.mark.asyncio
    async def test_call_tool(self):
        """call_tool should send tools/call and return result."""
        client = MCPClient(command="echo", args=["hello"])
        client._started = True
        client._process = _make_mock_process()

        mock_result = {
            "content": [{"type": "text", "text": "File contents here"}]
        }

        with patch.object(client, "_send_request", AsyncMock(return_value=mock_result)):
            result = await client.call_tool("read_file", {"path": "/tmp/test"})

        assert "content" in result
        assert result["content"][0]["text"] == "File contents here"

    @pytest.mark.asyncio
    async def test_list_resources(self):
        """list_resources should parse resources/list response."""
        client = MCPClient(command="echo", args=["hello"])
        client._started = True
        client._process = _make_mock_process()

        mock_result = {
            "resources": [
                {"uri": "file:///test.txt", "name": "test.txt"},
            ]
        }

        with patch.object(client, "_send_request", AsyncMock(return_value=mock_result)):
            resources = await client.list_resources()

        assert len(resources) == 1
        assert resources[0]["name"] == "test.txt"

    @pytest.mark.asyncio
    async def test_list_resources_unexpected_error_logs_structured_error(self, caplog):
        client = MCPClient(command="echo", args=["hello"])
        caplog.set_level(logging.ERROR, logger="myagent.tools.mcp")

        with patch.object(client, "_send_request", AsyncMock(side_effect=ValueError("bad rpc"))):
            resources = await client.list_resources()

        assert resources == []
        record = next(record for record in caplog.records if record.name == "myagent.tools.mcp")
        assert record.category == "error"
        assert record.component == "mcp"
        assert record.context == "mcp.resources_list"
        assert record.exc_info is not None

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """shutdown should terminate subprocess and cancel reader."""
        client = MCPClient(command="echo", args=["hello"])
        client._started = True
        client._process = _make_mock_process()
        client._process.wait = AsyncMock(return_value=0)

        reader_task = asyncio.create_task(asyncio.sleep(0.01))
        client._reader_task = reader_task

        with patch.object(client, "_process") as mock_proc:
            mock_proc.terminate = AsyncMock()
            mock_proc.wait = AsyncMock(return_value=0)
            client._process = mock_proc
            await client.shutdown()

        assert client._started is False
