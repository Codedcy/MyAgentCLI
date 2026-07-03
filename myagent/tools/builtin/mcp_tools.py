"""Built-in MCP resource and prompt tools (G6).

Provides mcp_read_resource and mcp_get_prompt tools so the model can
interact with MCP resources and prompt templates, not just see them
in the system prompt.
"""

from __future__ import annotations

import json
import logging

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.mcp")


class MCPReadResourceTool:
    name = "mcp_read_resource"
    description = (
        "Read the content of an MCP resource by its URI. "
        "Resources are data sources exposed by MCP servers (files, "
        "database records, API endpoints, etc.). Use this when you "
        "need to fetch data referenced by an MCP resource URI listed "
        "in the system prompt's MCP Reference section."
    )
    parameters = {
        "type": "object",
        "properties": {
            "uri": {
                "type": "string",
                "description": (
                    "The URI of the resource to read, as shown in the "
                    "MCP Reference section of the system prompt. "
                    "e.g. 'file:///path/to/data' or 'db://users'"
                ),
            },
        },
        "required": ["uri"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        uri = params["uri"]

        # Find MCP clients from the registry context
        clients = self._get_mcp_clients(context)
        if not clients:
            return ToolResult(
                error="No MCP servers are connected. Resources are unavailable."
            )

        errors = []
        for client in clients:
            try:
                result = await client.read_resource(uri)
                # Resources/read returns {"contents": [...]}
                contents = result.get("contents", [])
                if contents:
                    # Format readable output
                    output_parts = [f"Resource: {uri}\n"]
                    for i, item in enumerate(contents):
                        text = item.get("text", "")
                        uri_item = item.get("uri", "")
                        mime = item.get("mimeType", "text/plain")
                        if text:
                            output_parts.append(
                                f"[{mime}]{' ' + uri_item if uri_item else ''}\n"
                                f"{text[:10000]}\n"
                            )
                        else:
                            output_parts.append(
                                f"Content item {i + 1}: {json.dumps(item)[:5000]}\n"
                            )
                    output = "\n".join(output_parts)
                    if len(output) > 15000:
                        output = output[:15000] + "\n... (truncated)"
                    return ToolResult(output=output)
                else:
                    errors.append(f"No content returned for URI '{uri}'")
            except Exception as e:
                errors.append(f"{getattr(client, 'command', 'unknown')}: {e}")
                logger.exception(
                    "MCP read_resource '%s' failed",
                    uri,
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": f"mcp_read_resource:{uri}",
                    },
                )

        if errors:
            return ToolResult(
                error=f"Failed to read resource '{uri}' from any MCP server:\n"
                       + "\n".join(f"  - {e}" for e in errors)
            )
        return ToolResult(error=f"Resource not found: {uri}")

    @staticmethod
    def _get_mcp_clients(context: ToolContext) -> list:
        """Get the list of active MCP clients from the tool context."""
        # Check context attributes for MCP clients
        mcp_clients = getattr(context, "mcp_clients", None)
        if not mcp_clients and hasattr(context, "tool_registry"):
            mcp_clients = getattr(context.tool_registry, "mcp_clients", [])
        return mcp_clients or []


class MCPGetPromptTool:
    name = "mcp_get_prompt"
    description = (
        "Invoke an MCP prompt template and get the rendered result. "
        "Prompts are reusable templates provided by MCP servers that "
        "generate structured messages. Use this when you need to invoke "
        "a prompt listed in the system prompt's MCP Reference section. "
        "Arguments are optional — provide them if the prompt template "
        "defines parameters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The name of the prompt to invoke, as shown in the "
                    "MCP Reference section. e.g. 'code-review' or "
                    "'generate-report'"
                ),
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Optional arguments to pass to the prompt template. "
                    "These depend on the specific prompt — check the "
                    "prompt's description for expected parameters."
                ),
            },
        },
        "required": ["name"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        name = params["name"]
        arguments = params.get("arguments")

        # Find MCP clients from the registry context
        clients = self._get_mcp_clients(context)
        if not clients:
            return ToolResult(
                error="No MCP servers are connected. Prompts are unavailable."
            )

        errors = []
        for client in clients:
            try:
                result = await client.get_prompt(name, arguments)
                # prompts/get returns messages and may include a description.
                messages = result.get("messages", [])
                desc = result.get("description", "")
                if messages:
                    output_parts = [f"Prompt: {name}"]
                    if desc:
                        output_parts.append(f"Description: {desc}")
                    output_parts.append("")
                    for msg in messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Content can be a list of content blocks
                            text_parts = []
                            for block in content:
                                if isinstance(block, dict):
                                    text_parts.append(block.get("text", str(block)))
                                else:
                                    text_parts.append(str(block))
                            content = "\n".join(text_parts)
                        output_parts.append(f"[{role}] {str(content)[:8000]}")
                    output = "\n".join(output_parts)
                    if len(output) > 15000:
                        output = output[:15000] + "\n... (truncated)"
                    return ToolResult(output=output)
                else:
                    errors.append(f"Server returned no messages for prompt '{name}'")
            except Exception as e:
                errors.append(f"{getattr(client, 'command', 'unknown')}: {e}")
                logger.exception(
                    "MCP get_prompt '%s' failed",
                    name,
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": f"mcp_get_prompt:{name}",
                    },
                )

        if errors:
            return ToolResult(
                error=f"Failed to get prompt '{name}' from any MCP server:\n"
                       + "\n".join(f"  - {e}" for e in errors)
            )
        return ToolResult(error=f"Prompt not found: {name}")

    @staticmethod
    def _get_mcp_clients(context: ToolContext) -> list:
        """Get the list of active MCP clients from the tool context."""
        mcp_clients = getattr(context, "mcp_clients", None)
        if not mcp_clients and hasattr(context, "tool_registry"):
            mcp_clients = getattr(context.tool_registry, "mcp_clients", [])
        return mcp_clients or []
