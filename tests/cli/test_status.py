from rich.console import Console

from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.status import AgentInspectorPane, StatusBar, SubAgentInfo
from myagent.config.schema import StatusPaneConfig


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


def test_disabled_status_pane_returns_none():
    pane = AgentInspectorPane(StatusPaneConfig(enabled=False))

    assert pane.get_renderable(terminal_columns=120) is None


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
