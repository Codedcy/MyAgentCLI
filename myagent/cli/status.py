"""Rich renderables for the agent inspector pane.

The REPL owns the live display. This module only produces renderables that can
be embedded in the shared layout.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import RenderableType

    from myagent.agent.runtime_status import RuntimeStatusModel

logger = logging.getLogger("myagent.cli.status")

DEFAULT_SECTIONS = ["session", "tokens", "goal", "subagents", "tools", "health"]
LEGACY_STATUS_BAR_ITEMS = ["subagents", "tokens", "thinking"]
LEGACY_SECTION_MAPPING = {
    "thinking": "thinking",
    "tokens": "tokens",
    "subagents": "subagents",
}
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class SubAgentInfo:
    """Legacy sub-agent detail accepted by old StatusBar.update call sites."""

    agent_id: str
    task_name: str = ""
    status: str = "running"
    progress_pct: float = 0.0
    result_summary: str = ""
    retry_count: int = 0
    max_retries: int = 0


class AgentInspectorPane:
    """Produces Rich renderables for agent runtime status."""

    def __init__(
        self,
        config: Any = None,
        status_model: RuntimeStatusModel | None = None,
    ) -> None:
        self.config = config
        self.status_model = status_model
        self._expanded = True
        self._legacy_subagents_active_explicit = False
        self._data: dict[str, Any] = {
            "session_id": "",
            "project_name": "",
            "model": "",
            "subagents_active": 0,
            "subagents_details": [],
            "tokens": 0,
            "thinking": "Think High",
            "retry_info": "",
        }

    def update(self, **kwargs: Any) -> None:
        """Update legacy status fields used before RuntimeStatusModel wiring."""

        if "subagents_active" in kwargs:
            self._legacy_subagents_active_explicit = True
        self._data.update(kwargs)

    def toggle(self) -> bool:
        """Toggle between full and rail rendering, returning expanded state."""

        self._expanded = not self._expanded
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Set whether the pane should prefer full rendering."""

        self._expanded = bool(expanded)

    def get_renderable(
        self,
        terminal_columns: int | None = None,
    ) -> RenderableType | None:
        """Return a Rich renderable for the shared layout."""

        if not self._is_enabled():
            return None

        try:
            from rich.console import Group
            from rich.panel import Panel
            from rich.text import Text
        except ImportError:
            logger.exception(
                "Rich renderables unavailable for agent inspector pane",
                extra={
                    "category": "error",
                    "component": "system",
                    "context": "import rich renderables for agent inspector pane",
                },
            )
            return None

        status = self._current_status()
        if self._should_render_rail(terminal_columns):
            return self._render_rail(status, Group, Panel, Text)
        return self._render_full(status, Group, Panel, Text)

    def _render_full(
        self,
        status: dict[str, Any],
        group_cls: Any,
        panel_cls: Any,
        text_cls: Any,
    ):
        lines: list[str] = []
        sections = self._sections()

        if "session" in sections or "thinking" in sections:
            lines.extend(self._session_lines(status, sections))
        if "tokens" in sections:
            lines.extend(self._token_lines(status))
        if "goal" in sections:
            lines.extend(self._goal_lines(status))
        if "subagents" in sections:
            lines.extend(self._subagent_lines(status))
        if "tools" in sections:
            lines.extend(self._tool_lines(status))
        if "health" in sections:
            lines.extend(self._health_lines(status))

        if not lines:
            lines.append("No status sections enabled")

        return panel_cls(
            group_cls(*(text_cls(line) for line in lines)),
            title="Agent Inspector",
            width=self._pane_width(),
        )

    def _render_rail(
        self,
        status: dict[str, Any],
        group_cls: Any,
        panel_cls: Any,
        text_cls: Any,
    ):
        markers = [
            self._rail_token_indicator(status),
            f"SA {status['subagents_active']}",
        ]
        if status["health"]["last_error"] or status["health"]["retry_info"]:
            markers.append("!")
        return panel_cls(
            group_cls(*(text_cls(marker) for marker in markers)),
            width=self._rail_width(markers),
            padding=(0, 0),
        )

    def _session_lines(self, status: dict[str, Any], sections: list[str]) -> list[str]:
        session = status["session"]
        parts = []
        if "session" in sections:
            if session["session_id"]:
                parts.append(f"Session: {self._short_text(session['session_id'], 28)}")
            if session["project_name"]:
                parts.append(f"Project: {self._short_text(session['project_name'], 24)}")
        lines = [" | ".join(parts)] if parts else []

        model = self._short_text(session["model"], 28)
        if "session" in sections and model:
            lines.append(f"Model: {model}")
        if ("session" in sections or "thinking" in sections) and session["thinking"]:
            lines.append(f"Thinking: {self._short_text(session['thinking'], 18)}")
        return lines

    def _token_lines(self, status: dict[str, Any]) -> list[str]:
        tokens = status["tokens"]
        if not any(
            tokens[key]
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "turn_total",
                "session_total",
                "context_usage",
                "context_window",
            )
        ):
            legacy_total = tokens.get("legacy_total", 0)
            return [f"Tokens: {self._format_int(legacy_total)}"] if legacy_total else []

        lines = [
            "Tokens: "
            f"Prompt {self._format_int(tokens['prompt_tokens'])} | "
            f"Completion {self._format_int(tokens['completion_tokens'])} | "
            f"Turn {self._format_int(tokens['turn_total'])} | "
            f"Session {self._format_int(tokens['session_total'])}"
        ]
        context = self._percent_text(tokens["context_usage"])
        if tokens["context_window"]:
            lines.append(
                f"Context: {context} of {self._format_int(tokens['context_window'])}"
            )
        else:
            lines.append(f"Context: {context}")
        return lines

    def _goal_lines(self, status: dict[str, Any]) -> list[str]:
        goal = status["goal"]
        if not goal["name"] and not goal["active"]:
            return []

        states = []
        if goal["achieved"]:
            states.append("achieved")
        elif goal["active"]:
            states.append("active")
        else:
            states.append("inactive")
        if goal["waiting_for_user"]:
            states.append("waiting")
        if goal["budget_used"] is not None or goal["budget_limit"] is not None:
            used = "-" if goal["budget_used"] is None else self._format_int(goal["budget_used"])
            limit = (
                "-"
                if goal["budget_limit"] is None
                else self._format_int(goal["budget_limit"])
            )
            states.append(f"budget {used}/{limit}")

        label = self._short_text(goal["name"] or "Goal", 38)
        return [f"Goal: {label} ({', '.join(states)})"]

    def _subagent_lines(self, status: dict[str, Any]) -> list[str]:
        subagents = status["subagents"]
        if not subagents:
            active = status["subagents_active"]
            return [f"Sub-agents: {active} active"] if active else []

        lines = [
            f"Sub-agents: {len(subagents)} total, {status['subagents_active']} active"
        ]
        for info in subagents:
            if isinstance(info, str):
                lines.append(f"- {self._short_text(info, 42)}")
                continue
            name = self._short_text(
                self._attr(info, "task_name") or self._attr(info, "agent_id") or "-",
                26,
            )
            state = self._short_text(self._attr(info, "status") or "running", 14)
            parts = [f"- {name}", state]
            progress = self._attr(info, "progress_pct", 0.0)
            if progress:
                parts.append(self._percent_text(progress))
            retry_count = self._attr(info, "retry_count", 0)
            max_retries = self._attr(info, "max_retries", 0)
            if retry_count or max_retries:
                retry = f"{retry_count}/{max_retries}" if max_retries else str(retry_count)
                parts.append(retry)
            summary = self._short_text(self._attr(info, "result_summary", ""), 34)
            if summary:
                parts.append(summary)
            lines.append(" | ".join(parts))
        return lines

    def _tool_lines(self, status: dict[str, Any]) -> list[str]:
        tools = status["tools"]
        if not tools:
            return []

        current = self._current_tool(tools)
        name = self._short_text(self._attr(current, "name") or "-", 24)
        state = self._short_text(self._attr(current, "status") or "unknown", 14)
        line = f"Current tool: {name} ({state})"
        summary = self._short_text(self._attr(current, "last_result_summary", ""), 34)
        if summary:
            line = f"{line} - {summary}"
        if self._attr(current, "permission_waiting", False):
            line = f"{line} - permission waiting"
        return [line]

    def _health_lines(self, status: dict[str, Any]) -> list[str]:
        health = status["health"]
        lines = []
        if health["retry_info"]:
            lines.append(f"Retry: {self._short_text(health['retry_info'], 42)}")
        if health["mcp_connected"] is not None:
            state = "connected" if health["mcp_connected"] else "disconnected"
            lines.append(f"MCP: {state}")
        if health["last_error"]:
            lines.append(f"Error: {self._short_text(health['last_error'], 42)}")
        return lines

    def _current_status(self) -> dict[str, Any]:
        if self.status_model is not None:
            snapshot = self.status_model.snapshot()
            subagents = list(snapshot.subagents)
            return {
                "session": {
                    "session_id": snapshot.session.session_id,
                    "project_name": snapshot.session.project_name,
                    "model": snapshot.session.model,
                    "thinking": snapshot.session.thinking,
                },
                "tokens": {
                    "prompt_tokens": snapshot.tokens.prompt_tokens,
                    "completion_tokens": snapshot.tokens.completion_tokens,
                    "turn_total": snapshot.tokens.turn_total,
                    "session_total": snapshot.tokens.session_total,
                    "context_usage": snapshot.tokens.context_usage,
                    "context_window": snapshot.tokens.context_window,
                    "legacy_total": 0,
                },
                "goal": {
                    "name": snapshot.goal.name,
                    "active": snapshot.goal.active,
                    "achieved": snapshot.goal.achieved,
                    "waiting_for_user": snapshot.goal.waiting_for_user,
                    "budget_used": snapshot.goal.budget_used,
                    "budget_limit": snapshot.goal.budget_limit,
                },
                "subagents": subagents,
                "subagents_active": self._active_subagent_count(subagents),
                "tools": list(snapshot.tools),
                "health": {
                    "retry_info": snapshot.health.retry_info,
                    "mcp_connected": snapshot.health.mcp_connected,
                    "last_error": snapshot.health.last_error,
                },
            }
        return self._legacy_status()

    def _legacy_status(self) -> dict[str, Any]:
        subagents = list(self._data.get("subagents_details") or [])
        token_total = self._data.get("tokens", 0) or 0
        return {
            "session": {
                "session_id": self._data.get("session_id", ""),
                "project_name": self._data.get("project_name", ""),
                "model": self._data.get("model", ""),
                "thinking": self._data.get("thinking", ""),
            },
            "tokens": {
                "prompt_tokens": self._data.get("prompt_tokens", 0) or 0,
                "completion_tokens": self._data.get("completion_tokens", 0) or 0,
                "turn_total": self._data.get("turn_total", 0) or 0,
                "session_total": self._data.get("session_total", 0) or 0,
                "context_usage": self._data.get("context_usage", 0.0) or 0.0,
                "context_window": self._data.get("context_window", 0) or 0,
                "legacy_total": token_total,
            },
            "goal": {
                "name": self._data.get("goal", "") or self._data.get("goal_name", ""),
                "active": bool(self._data.get("goal_active", False)),
                "achieved": bool(self._data.get("goal_achieved", False)),
                "waiting_for_user": bool(self._data.get("goal_waiting_for_user", False)),
                "budget_used": self._data.get("goal_budget_used"),
                "budget_limit": self._data.get("goal_budget_limit"),
            },
            "subagents": subagents,
            "subagents_active": self._legacy_subagents_active_count(subagents),
            "tools": self._legacy_tools(),
            "health": {
                "retry_info": self._data.get("retry_info", "") or "",
                "mcp_connected": self._data.get("mcp_connected"),
                "last_error": self._data.get("last_error", "") or "",
            },
        }

    def _legacy_tools(self) -> list[dict[str, Any]]:
        name = self._data.get("current_tool") or self._data.get("tool_name")
        if not name:
            return []
        return [
            {
                "name": name,
                "status": self._data.get("tool_status", "running"),
                "permission_waiting": bool(self._data.get("tool_permission_waiting", False)),
                "last_result_summary": self._data.get("tool_result_summary", ""),
            }
        ]

    def _current_tool(self, tools: list[Any]) -> Any:
        for tool in tools:
            if self._attr(tool, "permission_waiting", False):
                return tool
        for tool in tools:
            if self._attr(tool, "status") in {"running", "retrying", "waiting"}:
                return tool
        return tools[0]

    def _active_subagent_count(self, subagents: list[Any]) -> int:
        active_states = {"created", "running", "retrying"}
        return sum(1 for info in subagents if self._attr(info, "status") in active_states)

    def _legacy_subagents_active_count(self, subagents: list[Any]) -> int:
        if self._legacy_subagents_active_explicit:
            return self._data.get("subagents_active", 0) or 0
        return self._active_subagent_count(subagents)

    def _should_render_rail(self, terminal_columns: int | None) -> bool:
        if not self._expanded:
            return True
        if terminal_columns is None:
            return False
        return terminal_columns < self._int_config("collapse_below_columns", 120)

    def _rail_token_indicator(self, status: dict[str, Any]) -> str:
        tokens = status["tokens"]
        if tokens["context_usage"]:
            return self._percent_text(tokens["context_usage"])
        total = tokens["session_total"] or tokens["legacy_total"]
        return self._compact_number(total) if total else "0"

    def _is_enabled(self) -> bool:
        pane_config = self._pane_config()
        if hasattr(pane_config, "enabled"):
            return bool(pane_config.enabled)
        if hasattr(self.config, "show_status_bar"):
            return bool(self.config.show_status_bar)
        return True

    def _sections(self) -> list[str]:
        pane_config = self._pane_config()
        sections = getattr(pane_config, "sections", None)
        legacy_items = getattr(self.config, "status_bar_items", None)
        if self._should_use_legacy_sections(sections, legacy_items):
            return [
                LEGACY_SECTION_MAPPING[item]
                for item in legacy_items
                if item in LEGACY_SECTION_MAPPING
            ]
        if sections is not None:
            return list(sections)
        return list(DEFAULT_SECTIONS)

    def _should_use_legacy_sections(
        self,
        sections: Any,
        legacy_items: Any,
    ) -> bool:
        return (
            isinstance(legacy_items, list)
            and legacy_items != LEGACY_STATUS_BAR_ITEMS
            and sections == DEFAULT_SECTIONS
        )

    def _pane_config(self) -> Any:
        return getattr(self.config, "status_pane", self.config)

    def _pane_width(self) -> int:
        width = self._int_config("width", 34)
        min_width = self._int_config("min_width", 28)
        max_width = self._int_config("max_width", 48)
        return min(max(width, min_width), max_width)

    def _rail_width(self, markers: list[str]) -> int:
        widest_marker = max((len(marker) for marker in markers), default=1)
        return max(self._int_config("rail_width", 5), widest_marker + 2)

    def _int_config(self, name: str, default: int) -> int:
        value = getattr(self._pane_config(), name, default)
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            return int(value)
        return default

    def _attr(self, value: Any, name: str, default: Any = "") -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def _short_text(self, value: Any, max_chars: int) -> str:
        text = self._sanitize_text(value)
        if len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return text[: max_chars - 3].rstrip() + "..."

    def _sanitize_text(self, value: Any) -> str:
        text = str(value or "")
        text = ANSI_PATTERN.sub("", text)
        text = CONTROL_PATTERN.sub("", text)
        return " ".join(text.split())

    def _format_int(self, value: Any) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "0"

    def _compact_number(self, value: Any) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return "0"
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}m"
        if number >= 1_000:
            return f"{number / 1_000:.1f}k"
        return str(number)

    def _percent_text(self, value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if number <= 1.0:
            number *= 100
        return f"{number:.0f}%"


StatusBar = AgentInspectorPane

__all__ = ["AgentInspectorPane", "StatusBar", "SubAgentInfo"]
