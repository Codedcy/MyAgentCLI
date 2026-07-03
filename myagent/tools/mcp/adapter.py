"""MCP tool adapter — wraps MCP tool as Tool protocol.

Translates MCP's inputSchema to OpenAI function-calling parameters format
so that MCP-discovered tools can be used alongside built-in tools.

Design doc reference: §四 工具系统 — MCP Tool Adapter
"""

from __future__ import annotations

import json
import logging
from typing import Any

from myagent.tools.base import ToolContext, ToolResult
from myagent.tools.mcp.client import MCPClient

logger = logging.getLogger("myagent.tools.mcp.adapter")


class MCPToolAdapter:
    """Wraps an MCP tool to implement the Tool protocol.

    The adapter translates between:
    - MCP's inputSchema ↔ OpenAI function-calling parameters
    - MCPClient.call_tool() ↔ Tool.execute()

    MCP tools are assigned permission level 3 (network-write) by default
    because they typically interact with external servers and services
    whose behavior is not fully known to the local permission controller.
    """

    # Default permission level for MCP tools: network-write (audit #40)
    permission_level: int = 3

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
            logger.exception(
                "MCP tool adapter execution failed for %s",
                self.name,
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp_tool_adapter_execute",
                },
            )
            return ToolResult(output="", error=str(e))

    # ── internal ────────────────────────────────────────────────

    def _translate_schema(self, mcp_schema: dict) -> dict:
        """Translate MCP inputSchema to OpenAI function-calling parameters.

        MCP schema uses JSON Schema format.  Before translation the schema
        is pre-processed to:
        - Resolve ``$ref`` pointers against ``$defs`` / ``definitions``
        - Flatten ``oneOf`` / ``anyOf`` into an ``enum``-based description
          so the model can see the valid choices inline.
        """
        resolved = self._resolve_schema(mcp_schema, root=mcp_schema)
        if not resolved:
            return {"type": "object", "properties": {}}

        result: dict = {
            "type": resolved.get("type", "object"),
            "properties": resolved.get("properties", {}),
        }

        if "required" in resolved:
            result["required"] = resolved["required"]

        return result

    # ── $ref / oneOf resolution (audit #40) ──────────────────────

    def _resolve_schema(self, schema: dict, root: dict) -> dict:
        """Resolve ``$ref`` pointers and flatten ``oneOf``/``anyOf``.

        Handles the common MCP pattern where input schemas use JSON Schema
        constructs that OpenAI's function-calling format does not understand.
        """
        if not isinstance(schema, dict):
            return schema

        # Resolve $ref
        if "$ref" in schema:
            ref = schema["$ref"]
            resolved = self._resolve_ref(ref, root)
            if resolved is not None:
                return self._resolve_schema(resolved, root)

        # Process each value recursively
        result: dict = {}
        for key, value in schema.items():
            if key in ("oneOf", "anyOf"):
                result[key] = self._flatten_oneof(value, root)
            elif isinstance(value, dict):
                result[key] = self._resolve_schema(value, root)
            elif isinstance(value, list):
                result[key] = [
                    self._resolve_schema(item, root)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    def _resolve_ref(self, ref: str, root: dict) -> dict | None:
        """Resolve a local ``$ref`` pointer like ``#/$defs/Foo``.

        Only local references are supported (must start with ``#/``).
        Remote ``$ref`` URIs are silently ignored.
        """
        if not ref.startswith("#/"):
            return None

        parts = ref[2:].split("/")
        current: Any = root
        for part in parts:
            # Unescape JSON Pointer ~0 (~) and ~1 (/)
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None

        return current if isinstance(current, dict) else None

    def _flatten_oneof(self, alternatives: list, root: dict) -> list:
        """Flatten ``oneOf``/``anyOf`` alternatives, resolving any ``$ref``​s.

        Each alternative is resolved recursively so that the resulting list
        is self-contained — downstream consumers (e.g. LLM function-calling
        schemas) see concrete types rather than JSON Schema indirections.
        """
        flattened: list = []
        for alt in alternatives:
            if not isinstance(alt, dict):
                flattened.append(alt)
                continue
            resolved = self._resolve_schema(alt, root)
            flattened.append(resolved)
        return flattened
