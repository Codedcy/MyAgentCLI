"""MCP tool adapter — wraps MCP tool as Tool protocol.

Translates MCP's inputSchema to OpenAI function-calling parameters format
so that MCP-discovered tools can be used alongside built-in tools.

Design doc reference: §四 工具系统 — MCP Tool Adapter
"""

from __future__ import annotations

import json

from myagent.tools.base import ToolContext, ToolResult
from myagent.tools.mcp.client import MCPClient


class MCPToolAdapter:
    """Wraps an MCP tool to implement the Tool protocol.

    The adapter translates between:
    - MCP's inputSchema ↔ OpenAI function-calling parameters
    - MCPClient.call_tool() ↔ Tool.execute()
    """

    def __init__(self, raw_tool: dict, client: MCPClient):
        """Create adapter from MCP tool definition.

        Args:
            raw_tool: Raw tool dict from MCP tools/list response.
            client: The MCPClient managing the server that provides this tool.
        """
        self.name: str = raw_tool["name"]
        self.description: str = raw_tool.get("description", "")
        self._mcp_schema: dict = raw_tool.get("inputSchema", {})
        self._client = client
        self._parameters = self._translate_schema(self._mcp_schema)

    @property
    def parameters(self) -> dict:
        """OpenAI function-calling compatible parameters schema."""
        return self._parameters

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        """Execute the MCP tool by calling the MCP server.

        Args:
            params: Tool parameters from the model.
            context: Execution context (session, permissions, etc.).

        Returns:
            ToolResult with MCP response content or error.
        """
        try:
            result = await self._client.call_tool(self.name, params)
            # Extract text content from MCP response
            content_parts = result.get("content", [])
            output_parts = []
            for part in content_parts:
                if isinstance(part, dict) and "text" in part:
                    output_parts.append(part["text"])
                elif isinstance(part, str):
                    output_parts.append(part)
            output = "\n".join(output_parts) if output_parts else json.dumps(result)
            return ToolResult(output=output, metadata={"mcp_raw": result})
        except Exception as e:
            return ToolResult(output="", error=str(e))

    # ── internal ────────────────────────────────────────────────

    def _translate_schema(self, mcp_schema: dict) -> dict:
        """Translate MCP inputSchema to OpenAI function-calling parameters.

        MCP schema uses JSON Schema format. OpenAI function calling uses
        a slightly different structure. This method handles the common case
        of a type: object with properties.
        """
        if not mcp_schema:
            return {"type": "object", "properties": {}}

        # MCP inputSchema IS JSON Schema, which is close enough to
        # OpenAI's parameters format. The main differences:
        # - OpenAI requires "type" at the top level (MCP has it)
        # - MCP may have extra fields like "$schema" which OpenAI ignores
        result = {
            "type": mcp_schema.get("type", "object"),
            "properties": mcp_schema.get("properties", {}),
        }

        if "required" in mcp_schema:
            result["required"] = mcp_schema["required"]

        return result

