"""Tool protocol, context, and result types.

All tools (built-in + MCP) implement the Tool protocol. The registry
holds Tool instances and provides schemas for LLM function calling.

Design doc reference: §四 工具系统 — 统一接口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        output: The tool's output text (may be empty on error).
        error: Error message if execution failed, None otherwise.
        metadata: Arbitrary key-value for downstream use
                  (e.g., exit_code, file_path, tokens_used).
    """

    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """Context passed to every tool execution.

    Attributes:
        session_id: Current session ID.
        project_dir: Root directory of the project.
        permissions: PermissionController instance for access checks.
        config: Full AppConfig for tool behavior tuning.
        subagent_pool: SubAgentPool (optional — may be None if pool not started).
        working_dir: Current working directory (defaults to project_dir).
    """

    session_id: str
    project_dir: Path
    permissions: Any  # PermissionController (avoid circular import)
    config: Any  # AppConfig (avoid circular import)
    subagent_pool: Any | None = None
    working_dir: Path | None = None


@runtime_checkable
class Tool(Protocol):
    """Protocol that all tools must satisfy.

    Tools provide name, description, and JSON Schema parameters
    (OpenAI function-calling format), plus an async execute method.

    Usage:
        class ReadTool:
            name = "read"
            description = "Read a file"
            parameters = {"type": "object", "properties": {...}}

            async def execute(self, params: dict, context: ToolContext) -> ToolResult:
                ...
    """

    name: str
    description: str
    parameters: dict  # JSON Schema in OpenAI function calling format

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        ...
