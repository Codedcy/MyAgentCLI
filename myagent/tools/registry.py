"""ToolRegistry — unified tool registry for built-in + MCP tools.

Design doc reference: §四 工具系统
"""

from __future__ import annotations

from myagent.tools.base import Tool


class ToolRegistry:
    """Registry of all available tools (built-in + MCP).

    Provides lookup by name and schema generation for LLM function calling.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance.

        If a tool with the same name already exists, it is replaced.
        """
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name, or None if not found."""
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

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
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def get_schemas_for(self, names: list[str]) -> list[dict]:
        """Return tool schemas for a subset of tools (by name).

        Unknown names are silently skipped.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for name in names
            if (tool := self._tools.get(name))
        ]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
