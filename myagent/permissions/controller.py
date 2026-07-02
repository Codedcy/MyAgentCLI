"""Permission controller — 4-level access control with allow/deny lists.

Level mapping:
  0 = read (read, glob, grep, web_fetch, web_search, task_create, task_update)
  1 = write (write, edit, memory_write)
  2 = exec (bash, spawn_subagent)
  3 = network_write (MCP network tools)

Design doc reference: §五 权限/沙箱系统
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class PermissionResult(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# Tool name → permission level mapping
TOOL_LEVEL_MAP: dict[str, int] = {
    "read": 0,
    "glob": 0,
    "grep": 0,
    "web_fetch": 0,
    "web_search": 0,
    "task_create": 0,
    "task_update": 0,
    "write": 1,
    "edit": 1,
    "memory_write": 1,
    "bash": 2,
    "spawn_subagent": 2,
    "send_message": 2,
}


@dataclass
class AutoAllowConfig:
    levels: list[int]
    paths: list[str]
    commands: list[str]


@dataclass
class AutoDenyConfig:
    paths: list[str]
    commands: list[str]


class PermissionController:
    """Manages tool access control with allow/deny lists and confirmation."""

    def __init__(
        self,
        default_mode: Literal["ask", "allow_all"] = "ask",
        auto_allow: AutoAllowConfig | None = None,
        auto_deny: AutoDenyConfig | None = None,
    ):
        self.default_mode = default_mode
        self.auto_allow = auto_allow or AutoAllowConfig(
            levels=[0], paths=[], commands=[]
        )
        self.auto_deny = auto_deny or AutoDenyConfig(
            paths=[".env", "*.key", "*.pem"],
            commands=["sudo", "rm -rf /"],
        )
        self._skip_all = False

    def skip_all(self, value: bool = True) -> None:
        """Toggle --dangerously-skip-permissions mode."""
        self._skip_all = value

    def set_mode(self, mode: Literal["ask", "allow_all"]) -> None:
        """Switch default mode at runtime."""
        self.default_mode = mode

    def apply_runtime_rule(self, rule: str) -> None:
        """Parse natural-language rule into allow/deny lists.

        Examples:
            "git *" → auto_allow.commands: ["git *"]
            "allow all" → set_mode("allow_all")
        """
        rule = rule.strip().lower()
        if rule in ("allow all", "allow everything", "全部放行"):
            self.set_mode("allow_all")
        elif rule.startswith("deny "):
            self.auto_deny.commands.append(rule[5:])
        else:
            self.auto_allow.commands.append(rule)

    def check(
        self,
        tool_name: str,
        level: int | None = None,
        params: dict | None = None,
    ) -> PermissionResult:
        """Check permission for a tool call.

        Returns ALLOW, DENY, or ASK.
        """
        # Full trust mode
        if self._skip_all:
            return PermissionResult.ALLOW

        if level is None:
            level = self._get_level(tool_name)

        params = params or {}

        # 1. Check auto_deny (highest priority negative)
        if self._matches_deny(tool_name, params):
            return PermissionResult.DENY

        # 2. Check auto_allow
        if self._matches_allow(tool_name, level, params):
            return PermissionResult.ALLOW

        # 3. Default mode
        if self.default_mode == "allow_all":
            return PermissionResult.ALLOW

        return PermissionResult.ASK

    async def confirm(self, tool_name: str, params: dict | None = None) -> bool:
        """Interactive confirmation dialog. Returns True if user approves.

        Note: This is a stub that returns True by default for testing.
        In production, this uses Rich/Prompt for interactive UI.
        """
        # In production, this would display a Rich dialog and wait for user input.
        # No timeout — wait forever for user response (per design doc §五).
        return True

    # ── internal ────────────────────────────────────────────────

    def _get_level(self, tool_name: str) -> int:
        """Map tool name to permission level. Unknown tools default to level 3."""
        return TOOL_LEVEL_MAP.get(tool_name, 3)

    def _matches_allow(self, tool_name: str, level: int, params: dict) -> bool:
        """Check if tool matches auto_allow rules."""
        # Level-based
        if level in self.auto_allow.levels:
            return True

        # Path matching
        for path_pattern in self.auto_allow.paths:
            for key in ("file_path", "path", "pattern"):
                if key in params and fnmatch.fnmatch(str(params[key]), path_pattern):
                    return True

        # Command matching
        if tool_name == "bash" and "command" in params:
            command = params["command"]
            for cmd_pattern in self.auto_allow.commands:
                if fnmatch.fnmatch(command, cmd_pattern):
                    return True

        return False

    def _matches_deny(self, tool_name: str, params: dict) -> bool:
        """Check if tool matches auto_deny rules."""
        # Path deny matching
        for path_pattern in self.auto_deny.paths:
            for key in ("file_path", "path"):
                if key in params and fnmatch.fnmatch(str(params[key]), path_pattern):
                    return True

        # Command deny matching
        if tool_name == "bash" and "command" in params:
            command = params["command"]
            for cmd_pattern in self.auto_deny.commands:
                if fnmatch.fnmatch(command, cmd_pattern):
                    return True

        return False
