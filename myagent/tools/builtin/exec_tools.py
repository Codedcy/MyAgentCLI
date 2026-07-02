"""Built-in exec tool: bash."""

from __future__ import annotations

import asyncio

from myagent.tools.base import ToolContext, ToolResult


class BashTool:
    name = "bash"
    description = "Executes a bash command and returns its output."
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds (max 600000)",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of what this command does",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run the command in the background",
            },
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Bypass permission checks",
            },
        },
        "required": ["command"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        command = params["command"]
        timeout_ms = params.get("timeout", 120000)  # default 120s
        run_in_background = params.get("run_in_background", False)

        try:
            timeout_sec = min(timeout_ms, 600000) / 1000.0

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(context.working_dir or context.project_dir),
            )

            if run_in_background:
                return ToolResult(
                    output=f"Command started in background (pid={proc.pid})",
                    metadata={"pid": proc.pid, "background": True},
                )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                error_output = stderr.decode("utf-8", errors="replace")
                if error_output:
                    output += "\n[stderr]\n" + error_output

            return ToolResult(
                output=output.strip() or "(no output)",
                metadata={"exit_code": proc.returncode or 0},
            )
        except asyncio.TimeoutError:
            return ToolResult(error=f"Command timed out after {timeout_ms}ms")
        except Exception as e:
            return ToolResult(error=str(e))
