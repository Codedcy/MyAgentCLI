"""Built-in file tools: read, write, edit, glob."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from myagent.tools.base import ToolContext, ToolResult


class ReadTool:
    name = "read"
    description = "Reads a file from the local filesystem."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read",
            },
        },
        "required": ["file_path"],
    }

    _MAX_LINES_NO_RANGE = 2000

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params["file_path"])
        if not path.is_absolute():
            path = context.project_dir / path

        if not path.exists():
            return ToolResult(error=f"File not found: {path}")
        if path.is_dir():
            return ToolResult(error=f"Path is a directory: {path}")

        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            offset = params.get("offset", 0)
            limit = params.get("limit")
            original_total = len(lines)

            if offset or limit:
                start = offset or 0
                end = (start + limit) if limit else None
                lines = lines[start:end]
                content = "\n".join(lines)
            elif original_total > self._MAX_LINES_NO_RANGE:
                lines = lines[: self._MAX_LINES_NO_RANGE]
                content = "\n".join(lines)
                content += (
                    f"\n\n[... File truncated at {self._MAX_LINES_NO_RANGE} lines. "
                    f"Total: {original_total} lines. Use offset/limit to read more.]"
                )

            return ToolResult(
                output=content,
                metadata={
                    "file_path": str(path),
                    "total_lines": len(content.split("\n")),
                },
            )
        except UnicodeDecodeError:
            return ToolResult(error=f"Cannot read binary file as text: {path}")
        except Exception as e:
            return ToolResult(error=str(e))


class WriteTool:
    name = "write"
    description = "Writes a file to the local filesystem, overwriting if one exists."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    }

    _read_files: set[str] = set()  # Track files that were read (session-scoped)

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params["file_path"])
        if not path.is_absolute():
            path = context.project_dir / path

        content = params["content"]

        # Safety: if overwriting a file not previously read, warn
        if path.exists() and str(path) not in self._read_files:
            self._read_files.add(str(path))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(
                output=f"File written: {path}",
                metadata={"file_path": str(path), "size_bytes": len(content)},
            )
        except Exception as e:
            return ToolResult(error=str(e))


class EditTool:
    name = "edit"
    description = "Performs exact string replacement in a file."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false)",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = Path(params["file_path"])
        if not path.is_absolute():
            path = context.project_dir / path

        if not path.exists():
            return ToolResult(error=f"File not found: {path}")

        old = params["old_string"]
        new = params["new_string"]
        replace_all = params.get("replace_all", False)

        try:
            content = path.read_text(encoding="utf-8")
            if not replace_all:
                count = content.count(old)
                if count == 0:
                    return ToolResult(error=f"old_string not found in {path}")
                if count > 1:
                    return ToolResult(
                        error=f"old_string found {count} times in {path}. Use replace_all=true to replace all occurrences."
                    )
            new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
            path.write_text(new_content, encoding="utf-8")
            replacements = content.count(old)
            return ToolResult(
                output=f"File edited: {path} ({replacements} replacement(s))",
                metadata={"file_path": str(path), "replacements": replacements},
            )
        except Exception as e:
            return ToolResult(error=str(e))


class GlobTool:
    name = "glob"
    description = "Fast file pattern matching."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", context.project_dir))
        if not search_path.is_absolute():
            search_path = context.project_dir / search_path

        try:
            matches = sorted(search_path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            output = "\n".join(str(m) for m in matches[:200])  # limit results
            return ToolResult(
                output=output if output else "(no matches)",
                metadata={"count": len(matches), "pattern": pattern},
            )
        except Exception as e:
            return ToolResult(error=str(e))
