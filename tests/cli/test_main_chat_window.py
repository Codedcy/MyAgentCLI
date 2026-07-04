"""Tests for chat-window startup wiring in CLI main."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import prompt_toolkit.application
import pytest

import myagent.cli.chat_window as chat_window_module
from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.chat_window import ChatWindowController
from myagent.cli.status import AgentInspectorPane
from myagent.cli.transcript import TranscriptBuffer
from myagent.config.schema import (
    AppConfig,
    ChatWindowConfig,
    StatusPaneConfig,
    UIConfig,
)

cli_main = importlib.import_module("myagent.cli.main")


def test_build_chat_window_factory_returns_controller_with_shared_status():
    config = AppConfig(
        ui=UIConfig(
            status_pane=StatusPaneConfig(enabled=True),
            chat_window=ChatWindowConfig(
                enabled=True,
                scrollback_lines=123,
                follow_output="manual",
            ),
        )
    )
    status_model = RuntimeStatusModel()
    status_pane = AgentInspectorPane(config.ui.status_pane, status_model)
    completer = object()
    lexer = object()

    factory = cli_main._build_chat_window_factory(
        config,
        status_pane,
        status_model,
    )
    controller = factory(
        config=object(),
        transcript=object(),
        status_pane=object(),
        status_model=object(),
        completer=completer,
        lexer=lexer,
    )

    assert isinstance(controller, ChatWindowController)
    assert controller.config is config
    assert isinstance(controller.transcript, TranscriptBuffer)
    assert controller.transcript.max_lines == 123
    assert controller.transcript.follow_output == "manual"
    assert controller.status_pane is status_pane
    assert controller.status_model is status_model
    assert controller.completer is completer
    assert controller.lexer is lexer


def test_build_chat_window_factory_uses_full_config_for_status_and_input():
    config = AppConfig(
        ui=UIConfig(
            status_pane=StatusPaneConfig(enabled=False, toggle_key="f4"),
            chat_window=ChatWindowConfig(enabled=True),
        )
    )
    status_model = RuntimeStatusModel()

    factory = cli_main._build_chat_window_factory(config, None, status_model)
    controller = factory()

    assert controller.config is config
    assert controller.status_pane.config is config.ui.status_pane
    assert controller.status_pane.get_renderable() is None
    assert controller.input_controller._inspector_toggle_key() == "f4"


def test_build_chat_window_factory_returns_none_when_disabled():
    config = AppConfig(
        ui=UIConfig(chat_window=ChatWindowConfig(enabled=False))
    )
    status_model = RuntimeStatusModel()

    assert cli_main._build_chat_window_factory(config, None, status_model) is None


def test_is_one_shot_command_identifies_list_and_export_boundaries():
    assert cli_main._is_one_shot_command(
        cli_main.parse_args(["--list-sessions"])
    ) is True
    assert cli_main._is_one_shot_command(
        cli_main.parse_args(["--session", "2026-07-05-existing"])
    ) is True
    assert cli_main._is_one_shot_command(cli_main.parse_args([])) is False
    assert cli_main._is_one_shot_command(
        cli_main.parse_args(["--resume", "2026-07-05-existing"])
    ) is False


@pytest.mark.asyncio
async def test_async_main_help_exits_before_startup_wiring(monkeypatch):
    def fail_build_factory(*args, **kwargs):
        raise AssertionError("help must exit before chat factory wiring")

    class FailChatWindowController:
        def __init__(self, *args, **kwargs):
            raise AssertionError("help must not instantiate chat window")

    class FailApplication:
        def __init__(self, *args, **kwargs):
            raise AssertionError("help must not start prompt_toolkit application")

    monkeypatch.setattr(cli_main, "_build_chat_window_factory", fail_build_factory)
    monkeypatch.setattr(
        chat_window_module,
        "ChatWindowController",
        FailChatWindowController,
    )
    monkeypatch.setattr(prompt_toolkit.application, "Application", FailApplication)

    with pytest.raises(SystemExit) as exc:
        await cli_main.async_main(["--help"])

    assert exc.value.code == 0


@pytest.mark.asyncio
async def test_async_main_interactive_startup_passes_chat_window_factory(
    tmp_path,
    monkeypatch,
):
    config = AppConfig(
        ui=UIConfig(chat_window=ChatWindowConfig(enabled=True))
    )
    records = _install_light_startup(monkeypatch, config, tmp_path)
    expected_factory = object()

    def fake_build_factory(config_arg, status_pane, status_model):
        records["factory_config"] = config_arg
        records["factory_status_pane"] = status_pane
        records["factory_status_model"] = status_model
        return expected_factory

    monkeypatch.setattr(
        cli_main,
        "_build_chat_window_factory",
        fake_build_factory,
        raising=False,
    )

    result = await cli_main.async_main(["--project-dir", str(tmp_path)])

    assert result == 0
    assert len(records["repl_kwargs"]) == 1
    repl_kwargs = records["repl_kwargs"][0]
    assert repl_kwargs["chat_window_factory"] is expected_factory
    assert repl_kwargs["status_bar"] is records["factory_status_pane"]
    assert records["factory_config"] is config
    assert isinstance(records["factory_status_pane"], AgentInspectorPane)
    assert isinstance(records["factory_status_model"], RuntimeStatusModel)


@pytest.mark.asyncio
async def test_async_main_resume_passes_chat_window_factory_and_keeps_status_wiring(
    tmp_path,
    monkeypatch,
):
    config = AppConfig(
        ui=UIConfig(chat_window=ChatWindowConfig(enabled=True))
    )
    resumed = SimpleNamespace(
        id="2026-07-05-resumed",
        goal="restore me",
        goal_achieved=False,
    )
    records = _install_light_startup(
        monkeypatch,
        config,
        tmp_path,
        resumed_session=resumed,
    )
    expected_factory = object()

    def fake_build_factory(config_arg, status_pane, status_model):
        records["factory_config"] = config_arg
        records["factory_status_pane"] = status_pane
        records["factory_status_model"] = status_model
        return expected_factory

    monkeypatch.setattr(
        cli_main,
        "_build_chat_window_factory",
        fake_build_factory,
        raising=False,
    )

    result = await cli_main.async_main(
        ["--resume", resumed.id, "--project-dir", str(tmp_path)]
    )

    assert result == 0
    assert records["subagent_session"] is resumed
    assert len(records["repl_kwargs"]) == 1
    repl_kwargs = records["repl_kwargs"][0]
    assert repl_kwargs["chat_window_factory"] is expected_factory
    assert repl_kwargs["status_bar"] is records["factory_status_pane"]
    assert records["repl_instances"][0]._current_session is resumed

    status_snapshot = records["factory_status_model"].snapshot()
    assert status_snapshot.session.session_id == resumed.id
    assert status_snapshot.goal.name == "restore me"
    assert status_snapshot.goal.active is True
    assert status_snapshot.goal.achieved is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "argv",
    [
        ["--list-sessions"],
        ["--session", "2026-07-05-existing", "--export", "markdown"],
    ],
)
async def test_async_main_one_shot_commands_do_not_build_chat_window_factory(
    tmp_path,
    monkeypatch,
    argv,
):
    config = AppConfig(
        ui=UIConfig(chat_window=ChatWindowConfig(enabled=True))
    )
    records = _install_light_startup(monkeypatch, config, tmp_path)

    def fail_factory(*args, **kwargs):
        raise AssertionError("one-shot command must not build chat window factory")

    class FailChatWindowController:
        def __init__(self, *args, **kwargs):
            raise AssertionError("one-shot command must not instantiate chat window")

    class FailApplication:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "one-shot command must not start prompt_toolkit application"
            )

    monkeypatch.setattr(
        cli_main,
        "_build_chat_window_factory",
        fail_factory,
        raising=False,
    )
    monkeypatch.setattr(
        chat_window_module,
        "ChatWindowController",
        FailChatWindowController,
    )
    monkeypatch.setattr(prompt_toolkit.application, "Application", FailApplication)

    result = await cli_main.async_main([*argv, "--project-dir", str(tmp_path)])

    assert result == 0
    assert records["repl_kwargs"] == []


@pytest.mark.asyncio
async def test_async_main_chat_startup_fallback_preserves_session_lifecycle(
    tmp_path,
    monkeypatch,
):
    config = AppConfig(
        ui=UIConfig(chat_window=ChatWindowConfig(enabled=True))
    )
    records = _install_light_startup(
        monkeypatch,
        config,
        tmp_path,
        repl_kind="chat_startup_fallback",
    )

    def failing_chat_factory(**kwargs):
        records["factory_kwargs"] = kwargs
        raise RuntimeError("chat startup failed")

    monkeypatch.setattr(
        cli_main,
        "_build_chat_window_factory",
        lambda *args: failing_chat_factory,
        raising=False,
    )

    result = await cli_main.async_main(["--project-dir", str(tmp_path)])

    assert result == 0
    assert records["prompt_loop_ran"] is True
    assert records["session_events"] == [
        ("start", tmp_path),
        ("end", records["started_session"]),
    ]
    assert records["repl_kwargs"][0]["chat_window_factory"] is failing_chat_factory


def _install_light_startup(
    monkeypatch,
    config: AppConfig,
    project_dir: Path,
    *,
    resumed_session=None,
    repl_kind: str = "basic",
) -> dict:
    """Replace heavy startup dependencies with small fakes."""

    records: dict = {
        "repl_kwargs": [],
        "repl_instances": [],
        "subagent_session": None,
        "session_events": [],
    }

    import myagent.agent.engine as engine_module
    import myagent.agent.goal as goal_module
    import myagent.agent.project as project_module
    import myagent.agent.session as session_module
    import myagent.cli.commands as commands_module
    import myagent.cli.renderer as renderer_module
    import myagent.cli.repl as repl_module
    import myagent.config.loader as loader_module
    import myagent.context.builder as context_builder_module
    import myagent.context.compression as compression_module
    import myagent.context.persistence as persistence_module
    import myagent.llm.provider as llm_module
    import myagent.logging.logger as logger_module
    import myagent.memory.dream as dream_module
    import myagent.memory.store as memory_module
    import myagent.permissions.controller as permissions_module
    import myagent.skills.registry as skills_module
    import myagent.subagent.pool as pool_module
    import myagent.tools.base as tools_base_module
    import myagent.tools.registry as tools_registry_module

    class FakeConfigLoader:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, cli_args=None):
            records["cli_args"] = cli_args
            return config

    class FakeProjectDetector:
        async def detect(self, detected_project_dir):
            assert detected_project_dir == project_dir
            return SimpleNamespace(project_hash="project-hash")

    class FakeToolRegistry:
        def __init__(self):
            self.mcp_clients = []

        def register(self, *args, **kwargs):
            pass

    class FakePermissionController:
        def __init__(self, *args, **kwargs):
            self.skip_all_enabled = False

        def skip_all(self, enabled):
            self.skip_all_enabled = enabled

    class FakeSubAgentPool:
        def __init__(self, *args, **kwargs):
            self._callbacks = []
            self._agents = {}

        def on_status_change(self, callback):
            self._callbacks.append(callback)

        async def spawn(self, *args, **kwargs):
            return SimpleNamespace(id="sub-001", status="running")

        def set_session(self, session, session_store):
            records["subagent_session"] = session
            records["subagent_session_store"] = session_store

    class FakeSkillRegistry:
        def __init__(self, *args, **kwargs):
            pass

        async def discover(self):
            pass

    class FakeSessionManager:
        def __init__(self, *args, **kwargs):
            self.session_store = None
            pass

        def estimate_total_rounds(self, since_timestamp=None):
            return 0

        async def start_new(self, start_project_dir):
            session = SimpleNamespace(
                id="2026-07-05-new",
                project_name=start_project_dir.name,
                project_hash="project-hash",
                goal=None,
                goal_achieved=None,
            )
            records["started_session"] = session
            records["session_events"].append(("start", start_project_dir))
            return session

        async def end_session(self, session):
            records["session_events"].append(("end", session))

        async def list_sessions(self, listed_project_dir):
            records["listed_project_dir"] = listed_project_dir
            return []

        async def export_session(self, session_id, export_format, export_project_dir):
            records["export"] = (session_id, export_format, export_project_dir)
            return project_dir / f"{session_id}.{export_format}"

        async def resume(self, session_id, resume_project_dir):
            records["resume"] = (session_id, resume_project_dir)
            return resumed_session

    class FakeDreamEngine:
        def __init__(self, *args, **kwargs):
            pass

        def touch_session_start(self):
            pass

        def _load_state(self):
            return {}

        def should_run(self, rounds):
            return False

    class FakeGoalTracker:
        def __init__(self, *args, **kwargs):
            self.goal = None

        def set_goal(self, goal):
            self.goal = goal

    class FakeREPLEngine:
        def __init__(self, **kwargs):
            records["repl_kwargs"].append(kwargs)
            records["repl_instances"].append(self)
            self._session_mgr = kwargs.get("session_mgr")
            self._project_dir = kwargs.get("project_dir")
            self._chat_window_factory = kwargs.get("chat_window_factory")
            self._current_session = None

        async def run(self):
            records["repl_ran"] = True
            if repl_kind == "chat_startup_fallback":
                if self._session_mgr is not None and self._current_session is None:
                    self._current_session = await self._session_mgr.start_new(
                        self._project_dir
                    )
                try:
                    self._chat_window_factory()
                except RuntimeError:
                    records["prompt_loop_ran"] = True
                if self._session_mgr is not None and self._current_session is not None:
                    await self._session_mgr.end_session(self._current_session)

    monkeypatch.setattr(loader_module, "ConfigLoader", FakeConfigLoader)
    monkeypatch.setattr(project_module, "ProjectDetector", FakeProjectDetector)
    monkeypatch.setattr(logger_module.LogManager, "setup", lambda *a, **kw: None)
    monkeypatch.setattr(
        logger_module.LogManager,
        "log_startup",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(tools_registry_module, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(cli_main, "_register_builtin_tools", lambda registry: None)

    async def fake_startup_mcp_servers(tool_registry, startup_project_dir):
        records["mcp_project_dir"] = startup_project_dir
        return []

    monkeypatch.setattr(
        cli_main,
        "_startup_mcp_servers",
        fake_startup_mcp_servers,
    )
    monkeypatch.setattr(
        permissions_module,
        "PermissionController",
        FakePermissionController,
    )
    monkeypatch.setattr(llm_module, "LLMProvider", lambda *a, **kw: object())
    monkeypatch.setattr(persistence_module, "SessionStore", lambda *a, **kw: object())
    monkeypatch.setattr(pool_module, "SubAgentPool", FakeSubAgentPool)
    monkeypatch.setattr(memory_module, "MemoryStore", lambda *a, **kw: object())
    monkeypatch.setattr(
        tools_base_module,
        "ToolContext",
        lambda *a, **kw: SimpleNamespace(),
    )
    monkeypatch.setattr(skills_module, "SkillRegistry", FakeSkillRegistry)
    monkeypatch.setattr(
        context_builder_module,
        "ContextBuilder",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        compression_module,
        "CompressionEngine",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(session_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(dream_module, "DreamEngine", FakeDreamEngine)
    monkeypatch.setattr(goal_module, "GoalTracker", FakeGoalTracker)
    monkeypatch.setattr(engine_module, "AgentEngine", lambda *a, **kw: object())
    monkeypatch.setattr(commands_module, "CommandDispatcher", lambda *a, **kw: object())
    monkeypatch.setattr(renderer_module, "Renderer", lambda *a, **kw: object())
    monkeypatch.setattr(repl_module, "REPLEngine", FakeREPLEngine)
    monkeypatch.setattr(cli_main, "_print_sessions_rich", lambda *a, **kw: None)

    return records
