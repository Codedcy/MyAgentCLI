"""Built-in search tool: grep (ripgrep)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from myagent.tools.base import ToolContext, ToolResult


class GrepTool:
    name = "grep"
    description = "Content search using ripgrep. Supports full regex syntax, file type filtering, and context lines."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (defaults to project dir)",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode (default: files_with_matches)",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files",
            },
            "-A": {"type": "integer", "description": "Lines after match"},
            "-B": {"type": "integer", "description": "Lines before match"},
            "-C": {"type": "integer", "description": "Context lines (before + after)"},
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        pattern = params["pattern"]
        search_path = params.get("path", str(context.project_dir))

        # Find ripgrep executable
        rg_path = shutil.which("rg")
        if not rg_path:
            return ToolResult(
                error="ripgrep (rg) not found. Install it from https://github.com/BurntSushi/ripgrep"
            )

        cmd = [rg_path, "--no-heading", "--with-filename", "--line-number", "--color=never"]

        # Output mode
        output_mode = params.get("output_mode", "files_with_matches")
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")

        # Context
        if "head_limit" in params and params["head_limit"]:
            # rg doesn't have --head-limit directly, use -m to limit matches
            cmd.extend(["-m", str(params["head_limit"])])

        if "-A" in params:
            cmd.extend(["-A", str(params["-A"])])
        if "-B" in params:
            cmd.extend(["-B", str(params["-B"])])
        if "-C" in params:
            cmd.extend(["-C", str(params["-C"])])
        if params.get("-i"):
            cmd.append("-i")
        if "glob" in params:
            cmd.extend(["--glob", params["glob"]])

        cmd.append(pattern)
        cmd.append(search_path)

        timeout = 120
        if context.config:
            timeout = context.config.tools.shell_timeout_seconds

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if proc.returncode == 1:
                # rg returns 1 when no matches found
                return ToolResult(output="(no matches)", metadata={"exit_code": 1})
            elif proc.returncode != 0:
                return ToolResult(error=stderr.decode().strip(), metadata={"exit_code": proc.returncode})

            output = stdout.decode("utf-8", errors="replace")
            return ToolResult(
                output=output.strip() or "(no matches)",
                metadata={"exit_code": 0},
            )
        except asyncio.TimeoutError:
            return ToolResult(error=f"grep timed out after {timeout}s")
        except Exception as e:
            return ToolResult(error=str(e))
