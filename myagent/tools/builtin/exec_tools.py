"""Built-in exec tool: bash."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from myagent.tools.base import ToolContext, ToolResult
from myagent.utils.text_decode import decode_tool_output

logger = logging.getLogger("myagent.tools.exec")

WINDOWS_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
)

POSIX_COMMAND_PATTERNS = (
    re.compile(r"(?:^|[;&|]\s*)mkdir\s+-p(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)touch(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)rm\s+-[A-Za-z]*[rf][A-Za-z]*(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)cp\s+-[A-Za-z]*[rR][A-Za-z]*(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)chmod(?:\s|$)"),
    re.compile(r"(?:^|[;&|]\s*)export\s+[A-Za-z_][A-Za-z0-9_]*="),
    re.compile(r"<<\s*['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?"),
    re.compile(r"\$\([^)]+\)"),
    re.compile(r"`[^`]+`"),
    re.compile(r"/dev/null"),
)


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _resolve_bash_executable() -> str | None:
    configured = os.environ.get("MYAGENT_BASH")
    if configured:
        configured_path = Path(configured)
        if configured_path.exists():
            return str(configured_path)

    discovered = shutil.which("bash")
    if discovered:
        return discovered

    if _is_windows():
        for candidate in WINDOWS_BASH_CANDIDATES:
            candidate_path = Path(candidate)
            if candidate_path.exists():
                return str(candidate_path)

    return None


def _looks_posix_specific(command: str) -> bool:
    return any(pattern.search(command) for pattern in POSIX_COMMAND_PATTERNS)


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
            cwd = str(context.working_dir or context.project_dir)
            bash_executable = _resolve_bash_executable()
            shell_name = "native"

            if bash_executable:
                proc = await asyncio.create_subprocess_exec(
                    bash_executable,
                    "-lc",
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                shell_name = "bash"
            else:
                if _is_windows() and _looks_posix_specific(command):
                    return ToolResult(
                        error=(
                            "Bash executable not found on Windows. Install Git Bash, "
                            "add bash.exe to PATH, or set MYAGENT_BASH to the full "
                            "bash.exe path before running POSIX shell commands."
                        ),
                        metadata={"shell": "missing-bash"},
                    )
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )

            if run_in_background:
                return ToolResult(
                    output=f"Command started in background (pid={proc.pid})",
                    metadata={"pid": proc.pid, "background": True, "shell": shell_name},
                )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            output = decode_tool_output(stdout)
            if stderr:
                error_output = decode_tool_output(stderr)
                if error_output:
                    output += "\n[stderr]\n" + error_output

            return ToolResult(
                output=output.strip() or "(no output)",
                metadata={"exit_code": proc.returncode or 0, "shell": shell_name},
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
