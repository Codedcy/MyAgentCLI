"""Tests for session tools: task_create, task_update."""

import logging

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.session_tools import (
    TaskCreateTool,
    TaskList,
    TaskUpdateTool,
    get_task_list,
    reset_task_list,
)


def make_ctx():
    return ToolContext(
        session_id="test",
        project_dir=None,
        permissions=None,
        config=None,
    )


class TestTaskList:
    def setup_method(self):
        reset_task_list()

    def test_create(self):
        tl = get_task_list()
        task = tl.create("Fix bug", "Fix the login bug")
        assert task.id == "1"
        assert task.subject == "Fix bug"
        assert task.status == "pending"

    def test_update(self):
        tl = get_task_list()
        task = tl.create("Test", "desc")
        updated = tl.update(task.id, status="in_progress")
        assert updated.status == "in_progress"

    def test_list_all(self):
        tl = get_task_list()
        tl.create("Task 1", "desc")
        tl.create("Task 2", "desc")
        assert len(tl.list_all()) == 2

    def test_delete(self):
        tl = get_task_list()
        task = tl.create("Task", "desc")
        tl.delete(task.id)
        assert tl.get(task.id) is None

    def test_corrupt_persisted_tasks_log_structured_error(self, tmp_path, caplog):
        persist_path = tmp_path / "tasks.json"
        persist_path.write_text("{not-json", encoding="utf-8")
        caplog.set_level(logging.ERROR, logger="myagent.tools.session")

        TaskList(persist_path=persist_path)

        record = next(record for record in caplog.records if record.name == "myagent.tools.session")
        assert record.category == "error"
        assert record.component == "tool"
        assert record.context == "task_list.load_from_disk"
        assert record.exc_info is not None


class TestTaskCreateTool:
    @pytest.mark.asyncio
    async def test_task_create(self):
        reset_task_list()
        tool = TaskCreateTool()
        result = await tool.execute(
            {"subject": "Test task", "description": "A test task"},
            make_ctx(),
        )
        assert result.error is None
        assert "Test task" in result.output
        assert result.metadata["task_id"] == "1"


class TestTaskUpdateTool:
    @pytest.mark.asyncio
    async def test_task_update_status(self):
        reset_task_list()
        # Create first
        create_tool = TaskCreateTool()
        create_result = await create_tool.execute(
            {"subject": "Test", "description": "desc"},
            make_ctx(),
        )

        update_tool = TaskUpdateTool()
        result = await update_tool.execute(
            {"taskId": "1", "status": "completed"},
            make_ctx(),
        )
        assert result.error is None
        assert "completed" in result.output

    @pytest.mark.asyncio
    async def test_task_update_nonexistent(self):
        reset_task_list()
        update_tool = TaskUpdateTool()
        result = await update_tool.execute(
            {"taskId": "999", "status": "completed"},
            make_ctx(),
        )
        assert result.error is not None
