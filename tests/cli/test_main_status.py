"""Tests for CLI startup status-model wiring."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.status import AgentInspectorPane
from myagent.config.schema import AppConfig, ModelConfig, StatusPaneConfig, UIConfig
from myagent.subagent.pool import AgentStatus

cli_main = importlib.import_module("myagent.cli.main")


class FakeHandle:
    def __init__(
        self,
        agent_id: str,
        status: AgentStatus,
        *,
        progress_iter: tuple[int, int] | None = None,
        retry_count: int = 0,
        max_retries: int = 0,
        output: str | None = None,
    ) -> None:
        self.id = agent_id
        self.status = status
        self._progress_iter = progress_iter
        self._retry_count = retry_count
        self._max_retries = max_retries
        self._result_data = (
            SimpleNamespace(output=output) if output is not None else None
        )


class FakeSubagentPool:
    def __init__(self) -> None:
        self._agents: dict[str, FakeHandle] = {}
        self._callbacks = []
        self.next_handle = FakeHandle("sub-001", AgentStatus.RUNNING)
        self.spawn_prompts: list[str] = []

    @property
    def active_count(self) -> int:
        active_states = {AgentStatus.CREATED, AgentStatus.RUNNING}
        return sum(
            1
            for handle in self._agents.values()
            if handle.status in active_states
        )

    def on_status_change(self, callback) -> None:
        self._callbacks.append(callback)

    async def spawn(self, prompt: str, *args, **kwargs):
        self.spawn_prompts.append(prompt)
        self._agents[self.next_handle.id] = self.next_handle
        return self.next_handle


def test_build_status_components_populates_session_status(tmp_path):
    config = AppConfig(
        model=ModelConfig(model="deepseek-v4-pro", thinking="Think Max")
    )
    project_dir = tmp_path / "MyAgentCLI"

    status_model, status_pane = cli_main._build_status_components(
        config, project_dir
    )

    assert isinstance(status_model, RuntimeStatusModel)
    assert isinstance(status_pane, AgentInspectorPane)
    assert status_pane.status_model is status_model
    snapshot = status_model.snapshot()
    assert snapshot.session.project_name == "MyAgentCLI"
    assert snapshot.session.model == "deepseek-v4-pro"
    assert snapshot.session.thinking == "Think Max"


def test_build_status_components_returns_model_without_disabled_pane(tmp_path):
    config = AppConfig(
        ui=UIConfig(
            status_pane=StatusPaneConfig(enabled=False),
            show_status_bar=True,
        )
    )

    status_model, status_pane = cli_main._build_status_components(
        config, tmp_path
    )

    assert isinstance(status_model, RuntimeStatusModel)
    assert status_pane is None


def test_build_status_components_uses_status_pane_enabled_over_legacy_flag(
    tmp_path,
):
    config = AppConfig(
        ui=UIConfig(
            status_pane=StatusPaneConfig(enabled=True),
            show_status_bar=False,
        )
    )

    _, status_pane = cli_main._build_status_components(config, tmp_path)

    assert isinstance(status_pane, AgentInspectorPane)


def test_extract_task_name_uses_first_line_and_twenty_char_truncation():
    assert cli_main._extract_task_name("Short task\nignored line") == "Short task"
    assert (
        cli_main._extract_task_name("123456789012345678901")
        == "123456789012345678.."
    )


@pytest.mark.asyncio
async def test_wire_subagent_status_wraps_spawn_and_records_task_name():
    status_model = RuntimeStatusModel()
    pool = FakeSubagentPool()
    pool.next_handle = FakeHandle(
        "sub-001",
        AgentStatus.RUNNING,
        progress_iter=(1, 4),
    )

    cli_main._wire_subagent_status(pool, status_model)
    handle = await pool.spawn("123456789012345678901\nSecond line")

    assert handle.id == "sub-001"
    assert len(pool._callbacks) == 1
    assert pool.spawn_prompts == ["123456789012345678901\nSecond line"]
    info = _subagents_by_id(status_model)["sub-001"]
    assert info.task_name == "123456789012345678.."
    assert info.status == "running"
    assert info.progress_pct == 0.25
    assert _active_subagent_count(status_model) == 1


@pytest.mark.asyncio
async def test_wire_subagent_status_updates_runtime_model_for_lifecycle_states():
    status_model = RuntimeStatusModel()
    pool = FakeSubagentPool()
    cli_main._wire_subagent_status(pool, status_model)
    callback = pool._callbacks[0]

    long_output = "abcdefghijklmnopqrstuvwxyz123456789"
    handles = {
        "running": FakeHandle(
            "running",
            AgentStatus.RUNNING,
            progress_iter=(3, 10),
        ),
        "retrying": FakeHandle(
            "retrying",
            AgentStatus.RUNNING,
            progress_iter=(2, 5),
            retry_count=2,
            max_retries=4,
        ),
        "completed": FakeHandle(
            "completed",
            AgentStatus.COMPLETED,
            output=long_output,
        ),
        "failed": FakeHandle("failed", AgentStatus.FAILED),
        "interrupted": FakeHandle("interrupted", AgentStatus.INTERRUPTED),
    }
    pool._agents.update(handles)
    pool._task_names.update(
        {
            "running": "Running task",
            "retrying": "Retrying task",
            "completed": "Completed task",
            "failed": "Failed task",
            "interrupted": "Interrupted task",
        }
    )

    for handle in handles.values():
        await callback(handle.id, handle.status, handle, pool)

    infos = _subagents_by_id(status_model)
    assert infos["running"].task_name == "Running task"
    assert infos["running"].status == "running"
    assert infos["running"].progress_pct == 0.3
    assert infos["retrying"].status == "retrying"
    assert infos["retrying"].progress_pct == 0.4
    assert infos["retrying"].retry_count == 2
    assert infos["retrying"].max_retries == 4
    assert infos["completed"].status == "completed"
    assert infos["completed"].result_summary == long_output[:28] + ".."
    assert infos["failed"].status == "failed"
    assert infos["interrupted"].status == "interrupted"
    assert _active_subagent_count(status_model) == 2

    pool._agents.pop("completed")
    await callback("completed", AgentStatus.RESULT_CONSUMED, None, pool)

    assert "completed" not in _subagents_by_id(status_model)


def _subagents_by_id(status_model: RuntimeStatusModel):
    return {
        subagent.agent_id: subagent
        for subagent in status_model.snapshot().subagents
    }


def _active_subagent_count(status_model: RuntimeStatusModel) -> int:
    active_states = {"created", "running", "retrying"}
    return sum(
        1
        for subagent in status_model.snapshot().subagents
        if subagent.status in active_states
    )
