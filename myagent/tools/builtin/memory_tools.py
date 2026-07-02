"""Built-in memory tool: memory_write."""

from __future__ import annotations

from myagent.tools.base import ToolContext, ToolResult


class MemoryWriteTool:
    name = "memory_write"
    description = "Write or update a memory file. One fact per file."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the memory file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write (frontmatter + markdown body)",
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        from pathlib import Path

        path = Path(params["file_path"])
        content = params["content"]

        # Validate basic frontmatter presence
        if not content.strip().startswith("---"):
            return ToolResult(
                error="Memory content must start with YAML frontmatter (---)"
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(
                output=f"Memory written: {path.name}",
                metadata={"file_path": str(path), "size_bytes": len(content)},
            )
        except Exception as e:
            return ToolResult(error=str(e))
