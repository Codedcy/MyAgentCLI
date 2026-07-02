"""Thread-safe context binding via contextvars.

Binds session_id and project_name to the current async context
so log records automatically include them without caller effort.

Design doc reference: §十一 日志系统
"""

from __future__ import annotations

from contextvars import ContextVar

_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_project_name: ContextVar[str | None] = ContextVar("project_name", default=None)


def set_context(session_id: str | None = None, project_name: str | None = None) -> None:
    """Bind session and project to the current async context."""
    if session_id is not None:
        _session_id.set(session_id)
    if project_name is not None:
        _project_name.set(project_name)


def clear_context() -> None:
    """Unbind session and project from the current async context."""
    _session_id.set(None)
    _project_name.set(None)


def get_context() -> dict[str, str | None]:
    """Get current session_id and project_name."""
    return {
        "session_id": _session_id.get(),
        "project": _project_name.get(),
    }
