"""Framework-neutral runtime status state for agent UI renderers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

_UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class SessionRuntimeStatus:
    session_id: str = ""
    project_name: str = ""
    model: str = ""
    thinking: str = ""


@dataclass(frozen=True, slots=True)
class TokenRuntimeStatus:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turn_total: int = 0
    session_total: int = 0
    context_usage: float = 0.0
    context_window: int = 0


@dataclass(frozen=True, slots=True)
class GoalRuntimeStatus:
    name: str = ""
    active: bool = False
    achieved: bool = False
    waiting_for_user: bool = False
    budget_used: int | None = None
    budget_limit: int | None = None


@dataclass(frozen=True, slots=True)
class SubAgentRuntimeInfo:
    agent_id: str = ""
    task_name: str = ""
    status: str = ""
    progress_pct: float = 0.0
    result_summary: str = ""
    retry_count: int = 0
    max_retries: int = 0
    duration_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ToolRuntimeStatus:
    name: str = ""
    status: str = ""
    permission_waiting: bool = False
    last_result_summary: str = ""
    duration_ms: float | None = None


@dataclass(frozen=True, slots=True)
class HealthRuntimeStatus:
    retry_info: str = ""
    mcp_connected: bool | None = None
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeStatusSnapshot:
    session: SessionRuntimeStatus = field(default_factory=SessionRuntimeStatus)
    tokens: TokenRuntimeStatus = field(default_factory=TokenRuntimeStatus)
    goal: GoalRuntimeStatus = field(default_factory=GoalRuntimeStatus)
    subagents: tuple[SubAgentRuntimeInfo, ...] = field(default_factory=tuple)
    tools: tuple[ToolRuntimeStatus, ...] = field(default_factory=tuple)
    health: HealthRuntimeStatus = field(default_factory=HealthRuntimeStatus)


class RuntimeStatusModel:
    """Mutable runtime status source for framework-specific renderers."""

    def __init__(self) -> None:
        self._session = SessionRuntimeStatus()
        self._tokens = TokenRuntimeStatus()
        self._goal = GoalRuntimeStatus()
        self._subagents: dict[str, SubAgentRuntimeInfo] = {}
        self._tools: dict[str, ToolRuntimeStatus] = {}
        self._health = HealthRuntimeStatus()

    def snapshot(self) -> RuntimeStatusSnapshot:
        return RuntimeStatusSnapshot(
            session=replace(self._session),
            tokens=replace(self._tokens),
            goal=replace(self._goal),
            subagents=tuple(replace(info) for info in self._subagents.values()),
            tools=tuple(replace(status) for status in self._tools.values()),
            health=replace(self._health),
        )

    def update_session(
        self,
        *,
        session_id: str | None = _UNSET,
        project_name: str | None = _UNSET,
        model: str | None = _UNSET,
        thinking: str | None = _UNSET,
    ) -> None:
        updates: dict[str, object] = {}
        if session_id is not _UNSET:
            updates["session_id"] = _text(session_id)
        if project_name is not _UNSET:
            updates["project_name"] = _text(project_name)
        if model is not _UNSET:
            updates["model"] = _text(model)
        if thinking is not _UNSET:
            updates["thinking"] = _text(thinking)
        if updates:
            self._session = replace(self._session, **updates)

    def update_tokens(
        self,
        *,
        prompt_tokens: int = _UNSET,
        completion_tokens: int = _UNSET,
        turn_total: int = _UNSET,
        session_total: int = _UNSET,
        context_usage: float = _UNSET,
        context_window: int = _UNSET,
    ) -> None:
        updates: dict[str, object] = {}
        if prompt_tokens is not _UNSET:
            updates["prompt_tokens"] = prompt_tokens
        if completion_tokens is not _UNSET:
            updates["completion_tokens"] = completion_tokens
        if turn_total is not _UNSET:
            updates["turn_total"] = turn_total
        if session_total is not _UNSET:
            updates["session_total"] = session_total
        if context_usage is not _UNSET:
            updates["context_usage"] = _clamp_percentage(context_usage)
        if context_window is not _UNSET:
            updates["context_window"] = context_window
        if updates:
            self._tokens = replace(self._tokens, **updates)

    def update_goal(
        self,
        *,
        name: str | None = _UNSET,
        active: bool = _UNSET,
        achieved: bool = _UNSET,
        waiting_for_user: bool = _UNSET,
        budget_used: int | None = _UNSET,
        budget_limit: int | None = _UNSET,
    ) -> None:
        updates: dict[str, object] = {}
        if name is not _UNSET:
            updates["name"] = _text(name)
        if active is not _UNSET:
            updates["active"] = active
        if achieved is not _UNSET:
            updates["achieved"] = achieved
        if waiting_for_user is not _UNSET:
            updates["waiting_for_user"] = waiting_for_user
        if budget_used is not _UNSET:
            updates["budget_used"] = budget_used
        if budget_limit is not _UNSET:
            updates["budget_limit"] = budget_limit
        if updates:
            self._goal = replace(self._goal, **updates)

    def upsert_subagent(
        self,
        agent_id: str,
        *,
        task_name: str | None = _UNSET,
        status: str | None = _UNSET,
        progress_pct: float = _UNSET,
        result_summary: str | None = _UNSET,
        retry_count: int = _UNSET,
        max_retries: int = _UNSET,
        duration_ms: float | None = _UNSET,
    ) -> None:
        key = _text(agent_id)
        current = self._subagents.get(key, SubAgentRuntimeInfo(agent_id=key))
        updates: dict[str, object] = {}
        if task_name is not _UNSET:
            updates["task_name"] = _text(task_name)
        if status is not _UNSET:
            updates["status"] = _text(status)
        if progress_pct is not _UNSET:
            updates["progress_pct"] = _clamp_percentage(progress_pct)
        if result_summary is not _UNSET:
            updates["result_summary"] = _text(result_summary)
        if retry_count is not _UNSET:
            updates["retry_count"] = retry_count
        if max_retries is not _UNSET:
            updates["max_retries"] = max_retries
        if duration_ms is not _UNSET:
            updates["duration_ms"] = duration_ms
        self._subagents[key] = replace(current, **updates)

    def remove_subagent(self, agent_id: str) -> None:
        self._subagents.pop(_text(agent_id), None)

    def update_tool(
        self,
        name: str,
        *,
        status: str | None = _UNSET,
        permission_waiting: bool = _UNSET,
        last_result_summary: str | None = _UNSET,
        duration_ms: float | None = _UNSET,
    ) -> None:
        key = _text(name)
        current = self._tools.get(key, ToolRuntimeStatus(name=key))
        updates: dict[str, object] = {}
        if status is not _UNSET:
            updates["status"] = _text(status)
        if permission_waiting is not _UNSET:
            updates["permission_waiting"] = permission_waiting
        if last_result_summary is not _UNSET:
            updates["last_result_summary"] = _text(last_result_summary)
        if duration_ms is not _UNSET:
            updates["duration_ms"] = duration_ms
        self._tools[key] = replace(current, **updates)

    def update_health(
        self,
        *,
        retry_info: str | None = _UNSET,
        mcp_connected: bool | None = _UNSET,
        last_error: str | None = _UNSET,
    ) -> None:
        updates: dict[str, object] = {}
        if retry_info is not _UNSET:
            updates["retry_info"] = _text(retry_info)
        if mcp_connected is not _UNSET:
            updates["mcp_connected"] = mcp_connected
        if last_error is not _UNSET:
            updates["last_error"] = _text(last_error)
        if updates:
            self._health = replace(self._health, **updates)

    def clear_transient(self) -> None:
        self._tools.clear()
        self._goal = replace(self._goal, waiting_for_user=False)
        self._health = replace(self._health, retry_info="", last_error="")


def _text(value: str | None) -> str:
    return "" if value is None else value


def _clamp_percentage(value: float) -> float:
    pct = float(value)
    if pct != pct:
        return 0.0
    return min(1.0, max(0.0, pct))


__all__ = [
    "GoalRuntimeStatus",
    "HealthRuntimeStatus",
    "RuntimeStatusModel",
    "RuntimeStatusSnapshot",
    "SessionRuntimeStatus",
    "SubAgentRuntimeInfo",
    "TokenRuntimeStatus",
    "ToolRuntimeStatus",
]
