"""Built-in session tools: task_create, task_update.

Implements in-memory task tracking with TaskList/TaskItem.
Persisted with session data.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Literal

from myagent.tools.base import ToolContext, ToolResult


@dataclass
class TaskItem:
    id: str
    subject: str
    description: str
    active_form: str | None = None
    status: Literal["pending", "in_progress", "completed", "deleted"] = "pending"
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskList:
    """In-memory task tracker, persisted per-session."""

    def __init__(self):
        self.tasks: dict[str, TaskItem] = {}
        self._counter = itertools.count(1)

    def create(self, subject: str, description: str, active_form: str | None = None) -> TaskItem:
        tid = str(next(self._counter))
        task = TaskItem(
            id=tid,
            subject=subject,
            description=description,
            active_form=active_form,
        )
        self.tasks[tid] = task
        return task

    def update(self, task_id: str, **kwargs) -> TaskItem:
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found")
        task = self.tasks[task_id]
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        return task

    def get(self, task_id: str) -> TaskItem | None:
        return self.tasks.get(task_id)

    def list_all(self) -> list[TaskItem]:
        return list(self.tasks.values())

    def delete(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)


# Global session-scoped task list (one per session)
# Replaced by engine on each session start
_current_task_list: TaskList | None = None


def get_task_list() -> TaskList:
    global _current_task_list
    if _current_task_list is None:
        _current_task_list = TaskList()
    return _current_task_list


def reset_task_list() -> None:
    global _current_task_list
    _current_task_list = TaskList()


class TaskCreateTool:
    name = "task_create"
    description = "Create a structured task for tracking progress."
    parameters = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "A brief title for the task",
            },
            "description": {
                "type": "string",
                "description": "What needs to be done",
            },
            "activeForm": {
                "type": "string",
                "description": "Present continuous form for status display",
            },
        },
        "required": ["subject", "description"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task = get_task_list().create(
            subject=params["subject"],
            description=params["description"],
            active_form=params.get("activeForm"),
        )
        return ToolResult(
            output=f"Task #{task.id} created: {task.subject}",
            metadata={"task_id": task.id, "status": task.status},
        )


class TaskUpdateTool:
    name = "task_update"
    description = "Update a task's status, subject, or other fields."
    parameters = {
        "type": "object",
        "properties": {
            "taskId": {
                "type": "string",
                "description": "The ID of the task to update",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "deleted"],
            },
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "activeForm": {"type": "string"},
        },
        "required": ["taskId"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task_id = params["taskId"]
        tl = get_task_list()
        if task_id not in tl.tasks:
            return ToolResult(error=f"Task {task_id} not found")

        update_kwargs = {}
        for field in ("status", "subject", "description", "activeForm"):
            if field in params:
                key = "active_form" if field == "activeForm" else field
                update_kwargs[key] = params[field]

        task = tl.update(task_id, **update_kwargs)
        return ToolResult(
            output=f"Task #{task.id} updated: {task.subject} [{task.status}]",
            metadata={"task_id": task.id, "status": task.status},
        )
