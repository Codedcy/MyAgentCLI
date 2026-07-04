from rich.console import Console

from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.status import AgentInspectorPane, StatusBar, SubAgentInfo
from myagent.config.schema import StatusPaneConfig, UIConfig


def render_text(renderable, *, width: int = 100) -> str:
    console = Console(record=True, width=width)
    console.print(renderable)
    return console.export_text(styles=False)


def test_full_mode_renders_runtime_status_snapshot_sections():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-1234567890",
        project_name="MyAgentCLI",
        model="deepseek-v4-pro",
        thinking="Think High",
    )
    model.update_tokens(
        prompt_tokens=1_200,
        completion_tokens=345,
        turn_total=1_545,
        session_total=98_765,
        context_usage=0.42,
        context_window=200_000,
    )
    model.update_goal(
        name="Ship inspector pane",
        active=True,
        achieved=False,
        waiting_for_user=True,
        budget_used=30,
        budget_limit=100,
    )
    model.upsert_subagent(
        "agent-1",
        task_name="review-auth",
        status="running",
        progress_pct=0.82,
    )
    model.upsert_subagent(
        "agent-2",
        task_name="review-api",
        status="completed",
        result_summary="2 issues",
    )
    model.update_tool(
        "shell_command",
        status="running",
        last_result_summary="pytest tests/cli/test_status.py -v",
    )
    model.update_health(
        retry_info="retry 2/3 after timeout",
        mcp_connected=False,
        last_error="LiteLLM timeout",
    )

    pane = AgentInspectorPane(
        StatusPaneConfig(width=44, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "Agent Inspector" in text
    assert "session-1234567890" in text
    assert "MyAgentCLI" in text
    assert "deepseek-v4-pro" in text
    assert "Think High" in text
    assert "Prompt" in text
    assert "1,200" in text
    assert "Completion" in text
    assert "345" in text
    assert "Turn" in text
    assert "1,545" in text
    assert "Session" in text
    assert "98,765" in text
    assert "Context" in text
    assert "42%" in text
    assert "Ship inspector pane" in text
    assert "waiting" in text
    assert "30/100" in text
    assert "review-auth" in text
    assert "running" in text
    assert "82%" in text
    assert "review-api" in text
    assert "completed" in text
    assert "2 issues" in text
    assert "shell_command" in text
    assert "retry 2/3 after timeout" in text
    assert "LiteLLM timeout" in text


def test_narrow_terminal_uses_rail_mode_without_long_task_names():
    model = RuntimeStatusModel()
    model.update_tokens(session_total=98_765, context_usage=0.42)
    model.upsert_subagent(
        "agent-1",
        task_name="InvestigateOverflowProneStreamingRendererTaskName",
        status="running",
        progress_pct=0.5,
    )
    model.update_health(retry_info="retry 1/3", last_error="timeout")
    pane = AgentInspectorPane(
        StatusPaneConfig(width=44, collapse_below_columns=120, rail_width=8),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=80), width=80)

    assert "Agent Inspector" not in text
    assert "InvestigateOverflowProneStreamingRendererTaskName" not in text
    assert "42%" in text
    assert "SA 1" in text
    assert "!" in text


def test_default_rail_mode_keeps_compact_markers_on_single_lines():
    model = RuntimeStatusModel()
    model.update_tokens(context_usage=0.42)
    model.upsert_subagent(
        "agent-1",
        task_name="Investigate overflow-prone rail rendering",
        status="running",
    )
    model.update_health(last_error="timeout")
    pane = AgentInspectorPane(StatusPaneConfig(), status_model=model)

    text = render_text(pane.get_renderable(terminal_columns=80), width=80)

    assert "42%" in text
    assert "SA 1" in text
    assert "!" in text


def test_model_backed_legacy_update_writes_visible_status_to_model():
    model = RuntimeStatusModel()
    pane = AgentInspectorPane(
        StatusPaneConfig(width=44, collapse_below_columns=80),
        status_model=model,
    )

    pane.update(tokens=123, retry_info="retrying request")

    snapshot = model.snapshot()
    assert snapshot.tokens.session_total == 123
    assert snapshot.health.retry_info == "retrying request"

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "123" in text
    assert "retrying request" in text


def test_preferred_width_uses_full_width_or_marker_aware_rail_width():
    pane = AgentInspectorPane(StatusPaneConfig(width=44, collapse_below_columns=120))

    assert pane.preferred_width(terminal_columns=160) == 44

    pane.update(subagents_active=123456789)
    assert pane.preferred_width(terminal_columns=80) == len("SA 123456789") + 2


def test_disabled_status_pane_returns_none():
    pane = AgentInspectorPane(StatusPaneConfig(enabled=False))

    assert pane.get_renderable(terminal_columns=120) is None


def test_dynamic_text_escapes_rich_markup_literals():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-1",
        project_name="[red]project[/red]",
        model="deepseek-v4-pro",
        thinking="Think High",
    )
    model.update_health(last_error="[red]boom[/red]")
    pane = AgentInspectorPane(
        StatusPaneConfig(width=48, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "[red]project[/red]" in text
    assert "[red]boom[/red]" in text


def test_dynamic_text_strips_ansi_and_control_characters():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="sid\x1b[31m-red\x1b[0m",
        project_name="Proj\x07Name",
        model="deepseek-v4-pro",
        thinking="Think High",
    )
    model.update_health(last_error="bad\x1b[31mred\x1b[0m\x08value")
    pane = AgentInspectorPane(
        StatusPaneConfig(width=48, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "\x1b" not in text
    assert "\x07" not in text
    assert "\x08" not in text
    assert "sid-red" in text
    assert "ProjName" in text
    assert "badredvalue" in text


def test_dynamic_text_collapses_multiline_values():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-1",
        project_name="Project\nName",
        model="deepseek-v4-pro",
        thinking="Think\tHigh",
    )
    model.update_tool(
        "shell_command",
        status="completed",
        last_result_summary="first line\r\nsecond\tline",
    )
    pane = AgentInspectorPane(
        StatusPaneConfig(width=48, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "Project Name" in text
    assert "Think High" in text
    assert "first line second line" in text


def test_long_subagent_text_is_truncated_in_full_mode():
    long_task_name = "TaskName" + ("x" * 160)
    long_summary = "Summary" + ("y" * 180)
    model = RuntimeStatusModel()
    model.upsert_subagent(
        "agent-1",
        task_name=long_task_name,
        status="completed",
        result_summary=long_summary,
    )
    pane = AgentInspectorPane(
        StatusPaneConfig(width=44, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert long_task_name not in text
    assert long_summary not in text
    assert "TaskName" in text
    assert "Summary" in text


def test_empty_sections_config_disables_all_sections():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-1",
        project_name="MyAgentCLI",
        model="deepseek-v4-pro",
        thinking="Think High",
    )
    model.update_tokens(session_total=100, context_usage=0.25)
    pane = AgentInspectorPane(
        StatusPaneConfig(sections=[], width=44, collapse_below_columns=80),
        status_model=model,
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "No status sections enabled" in text
    assert "deepseek-v4-pro" not in text
    assert "Tokens" not in text


def test_legacy_status_bar_items_override_default_status_pane_sections():
    config = UIConfig(status_bar_items=["tokens"])
    pane = StatusBar(config)
    pane.update(tokens=500, thinking="Think Max", subagents_active=2)

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "Tokens: 500" in text
    assert "Think Max" not in text
    assert "Sub-agents" not in text


def test_legacy_subagent_details_compute_active_count_when_not_explicitly_set():
    pane = StatusBar(StatusPaneConfig(width=44, collapse_below_columns=80))
    pane.update(
        subagents_details=[
            SubAgentInfo(
                agent_id="legacy-agent",
                task_name="legacy-task",
                status="running",
            )
        ],
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "1 total, 1 active" in text


def test_statusbar_alias_and_legacy_update_subagent_details_still_render():
    pane = StatusBar(StatusPaneConfig(width=44, collapse_below_columns=80))

    assert StatusBar is AgentInspectorPane

    pane.update(
        tokens=500,
        thinking="Think Max",
        retry_info="retrying request",
        subagents_active=1,
        subagents_details=[
            SubAgentInfo(
                agent_id="legacy-agent",
                task_name="legacy-task",
                status="retrying",
                retry_count=1,
                max_retries=3,
            ),
            "legacy string detail",
        ],
    )

    text = render_text(pane.get_renderable(terminal_columns=120), width=120)

    assert "Agent Inspector" in text
    assert "500" in text
    assert "Think Max" in text
    assert "retrying request" in text
    assert "legacy-task" in text
    assert "retrying" in text
    assert "1/3" in text
    assert "legacy string detail" in text


def test_cli_package_exports_agent_inspector_and_statusbar_alias():
    import myagent.cli as cli

    assert cli.AgentInspectorPane is AgentInspectorPane
    assert cli.StatusBar is AgentInspectorPane
