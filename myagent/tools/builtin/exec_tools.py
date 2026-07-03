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
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Bypass permission checks",
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
        dangerously_disable_sandbox = params.get("dangerouslyDisableSandbox", False)

        # Permission check: skip only if sandbox is explicitly disabled
        if not dangerously_disable_sandbox and context.permissions is not None:
            try:
                result = context.permissions.check(
                    tool_name="bash",
                    params=params,
                )
                if result.name == "DENY":
                    return ToolResult(
                        error="Permission denied: bash execution blocked by sandbox.",
                        metadata={"permission": "denied"},
                    )
                if result.name == "ASK":
                    allowed = await context.permissions.confirm(
                        tool_name="bash",
                        params=params,
                    )
                    if not allowed:
                        return ToolResult(
                            error="Permission denied: bash execution blocked by sandbox.",
                            metadata={"permission": "denied"},
                        )
            except Exception as e:
                logger.warning(
                    "Permission check failed for bash: %s", str(e),
                    extra={"category": "tool", "tool_name": "bash"},
                )
                # If permission system errors, deny by default for safety
                return ToolResult(
                    error=f"Permission check error: {e}",
                    metadata={"permission": "error"},
                )

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
