"""Built-in exec tool: bash."""

from __future__ import annotations

import asyncio
import logging

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.exec")


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
        },
        "required": ["command"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        command = params["command"]
        # Read config timeout if available, fall back to hardcoded 120000ms (gap-13)
        config_timeout_ms = 120000
        if context.config and hasattr(context.config, 'tools'):
            config_timeout_ms = context.config.tools.shell_timeout_seconds * 1000
        timeout_ms = params.get("timeout", config_timeout_ms)
        run_in_background = params.get("run_in_background", False)

        # Permission checks are handled centrally by the engine's _execute_tool
        # before calling tool.execute(). Tool implementations trust the engine's
        # pre-check and do not re-prompt the user (per spec §五).

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
        except TimeoutError:
            logger.exception(
                "Bash command timed out",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "execute shell command timeout",
                },
            )
            return ToolResult(error=f"Command timed out after {timeout_ms}ms")
        except Exception as e:
            logger.exception(
                "Bash tool failed",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "bash.execute",
                },
            )
            return ToolResult(error=str(e))
