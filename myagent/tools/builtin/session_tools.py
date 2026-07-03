"""Built-in session tools: task_create, task_update.

Implements in-memory task tracking with TaskList/TaskItem.
Persisted to disk alongside session data.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.session")


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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "active_form": self.active_form,
            "status": self.status,
            "owner": self.owner,
            "blocks": self.blocks,
            "blocked_by": self.blocked_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskItem:
        return cls(
            id=d.get("id", ""),
            subject=d.get("subject", ""),
            description=d.get("description", ""),
            active_form=d.get("active_form"),
            status=d.get("status", "pending"),
            owner=d.get("owner"),
            blocks=d.get("blocks", []),
            blocked_by=d.get("blocked_by", []),
            metadata=d.get("metadata", {}),
        )


class TaskList:
    """In-memory task tracker, persisted per-session to disk."""

    def __init__(self, persist_path: Path | None = None):
        self.tasks: dict[str, TaskItem] = {}
        self._counter = itertools.count(1)
        self._persist_path = persist_path
        self._load_from_disk()

    def create(
        self,
        subject: str,
        description: str,
        active_form: str | None = None,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskItem:
        tid = str(next(self._counter))
        task = TaskItem(
            id=tid,
            subject=subject,
            description=description,
            active_form=active_form,
            blocks=blocks or [],
            blocked_by=blocked_by or [],
            owner=owner,
            metadata=metadata or {},
        )
        self.tasks[tid] = task
        self._save_to_disk()
        return task

    def update(self, task_id: str, **kwargs) -> TaskItem:
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found")
        task = self.tasks[task_id]
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        self._save_to_disk()
        return task

    def get(self, task_id: str) -> TaskItem | None:
        return self.tasks.get(task_id)

    def list_all(self) -> list[TaskItem]:
        return list(self.tasks.values())

    def delete(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self._save_to_disk()

    def _save_to_disk(self) -> None:
        """Persist task list to disk as JSON."""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "next_id": next(self._counter, 1),
                "tasks": [t.to_dict() for t in self.tasks.values()],
            }
            self._persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception(
                "Failed to persist task list",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "task_list.save_to_disk",
                },
            )

    def _load_from_disk(self) -> None:
        """Load task list from disk on startup."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._counter = itertools.count(data.get("next_id", 1))
            for td in data.get("tasks", []):
                task = TaskItem.from_dict(td)
                self.tasks[task.id] = task
        except Exception:
            logger.exception(
                "Failed to load persisted task list; starting fresh",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "task_list.load_from_disk",
                },
            )


# Global session-scoped task list (one per session)
# Replaced by engine on each session start
_current_task_list: TaskList | None = None


def get_task_list() -> TaskList:
    global _current_task_list
    if _current_task_list is None:
        _current_task_list = TaskList()
    return _current_task_list


def reset_task_list(persist_path: Path | None = None) -> None:
    global _current_task_list
    _current_task_list = TaskList(persist_path=persist_path)


class TaskCreateTool:
    name = "task_create"
    description = "Create a structured task for tracking progress. Supports dependency tracking."
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
            "blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that this task blocks (dependent tasks)",
            },
            "blockedBy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that must complete before this task can start",
            },
            "owner": {
                "type": "string",
                "description": "Agent or user name assigned to this task",
            },
            "metadata": {
                "type": "object",
                "description": "Arbitrary metadata key-value pairs",
            },
        },
        "required": ["subject", "description"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task = get_task_list().create(
            subject=params["subject"],
            description=params["description"],
            active_form=params.get("activeForm"),
            blocks=params.get("blocks"),
            blocked_by=params.get("blockedBy"),
            owner=params.get("owner"),
            metadata=params.get("metadata"),
        )
        return ToolResult(
            output=f"Task #{task.id} created: {task.subject}",
            metadata={"task_id": task.id, "status": task.status},
        )


class TaskUpdateTool:
    name = "task_update"
    description = "Update a task's status, subject, dependencies, owner, or metadata."
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
            "blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that this task blocks (dependent tasks)",
            },
            "blockedBy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task IDs that must complete before this task can start",
            },
            "owner": {
                "type": "string",
                "description": "Agent or user name assigned to this task",
            },
            "metadata": {
                "type": "object",
                "description": "Arbitrary metadata key-value pairs to merge into existing metadata",
            },
        },
        "required": ["taskId"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task_id = params["taskId"]
        tl = get_task_list()
        if task_id not in tl.tasks:
            return ToolResult(error=f"Task {task_id} not found")

        # Map camelCase parameter names to snake_case internal field names
        field_mapping = {
            "status": "status",
            "subject": "subject",
            "description": "description",
            "activeForm": "active_form",
            "blocks": "blocks",
            "blockedBy": "blocked_by",
            "owner": "owner",
            "metadata": "metadata",
        }
        update_kwargs = {}
        for param_key, field_key in field_mapping.items():
            if param_key in params:
                update_kwargs[field_key] = params[param_key]

        # For metadata, merge into existing metadata rather than replace
        if "metadata" in params:
            existing = tl.tasks[task_id].metadata or {}
            merged = {**existing, **params["metadata"]}
            update_kwargs["metadata"] = merged

        task = tl.update(task_id, **update_kwargs)
        return ToolResult(
            output=f"Task #{task.id} updated: {task.subject} [{task.status}]",
            metadata={"task_id": task.id, "status": task.status},
        )
