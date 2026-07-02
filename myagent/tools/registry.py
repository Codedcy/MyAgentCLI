"""ToolRegistry — unified tool registry for built-in + MCP tools.

Design doc reference: §四 工具系统
"""

from __future__ import annotations

from dataclasses import dataclass

from myagent.tools.base import Tool


@dataclass
class ToolEntry:
    """Entry in the tool registry tracking the tool instance and its source.

    Attributes:
        tool: The tool instance implementing the Tool protocol.
        source: Origin of the tool — "builtin" or "mcp".
    """

    tool: Tool
    source: str  # "builtin" or "mcp"


class ToolRegistry:
    """Registry of all available tools (built-in + MCP).

    Provides lookup by name and schema generation for LLM function calling.
    Built-in tools take priority over MCP tools with the same name.
    """

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(self, tool: Tool, source: str = "builtin") -> None:
        """Register a tool instance.

        If a built-in tool with the same name already exists, an MCP
        registration is silently skipped. In all other cases (built-in
        over built-in, MCP over MCP, built-in over MCP), the existing
        entry is replaced.

        Args:
            tool: The tool instance to register.
            source: "builtin" (default) or "mcp".
        """
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            # Built-in tools take priority — don't overwrite with MCP
            if existing.source == "builtin" and source == "mcp":
                return
        self._tools[tool.name] = ToolEntry(tool=tool, source=source)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name, or None if not found."""
        entry = self._tools.get(name)
        return entry.tool if entry else None

    def get_source(self, name: str) -> str | None:
        """Return the source of a registered tool (``"builtin"``, ``"mcp"``), or None."""
        entry = self._tools.get(name)
        return entry.source if entry else None

    def list_all(self) -> list[Tool]:
        """Return all registered tools."""
        return [entry.tool for entry in self._tools.values()]

    def get_schemas(self) -> list[dict]:
        """Return all tool schemas in OpenAI function-calling format.

        Returns:
            List of dicts with structure:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": entry.tool.name,
                    "description": entry.tool.description,
                    "parameters": entry.tool.parameters,
                },
            }
            for entry in self._tools.values()
        ]

    def get_schemas_for(self, names: list[str]) -> list[dict]:
        """Return tool schemas for a subset of tools (by name).

        Unknown names are silently skipped.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": entry.tool.name,
                    "description": entry.tool.description,
                    "parameters": entry.tool.parameters,
                },
            }
            for name in names
            if (entry := self._tools.get(name))
        ]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
