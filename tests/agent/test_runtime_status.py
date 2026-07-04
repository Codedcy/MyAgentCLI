import pytest

from myagent.agent import (
    RuntimeStatusModel as ExportedRuntimeStatusModel,
)
from myagent.agent import (
    RuntimeStatusSnapshot as ExportedRuntimeStatusSnapshot,
)
from myagent.agent.runtime_status import (
    GoalRuntimeStatus,
    HealthRuntimeStatus,
    RuntimeStatusModel,
    RuntimeStatusSnapshot,
    SessionRuntimeStatus,
    SubAgentRuntimeInfo,
    TokenRuntimeStatus,
    ToolRuntimeStatus,
)


def test_runtime_status_exports_are_available_from_agent_package():
    assert ExportedRuntimeStatusModel is RuntimeStatusModel
    assert ExportedRuntimeStatusSnapshot is RuntimeStatusSnapshot


def test_default_snapshot_values():
    model = RuntimeStatusModel()

    snapshot = model.snapshot()

    assert snapshot == RuntimeStatusSnapshot(
        session=SessionRuntimeStatus(
            session_id="",
            project_name="",
            model="",
            thinking="",
        ),
        tokens=TokenRuntimeStatus(
            prompt_tokens=0,
            completion_tokens=0,
            turn_total=0,
            session_total=0,
            context_usage=0.0,
            context_window=0,
        ),
        goal=GoalRuntimeStatus(
            name="",
            active=False,
            achieved=False,
            waiting_for_user=False,
            budget_used=None,
            budget_limit=None,
        ),
        subagents=(),
        tools=(),
        health=HealthRuntimeStatus(
            retry_info="",
            mcp_connected=None,
            last_error="",
        ),
    )


def test_token_updates_clamp_context_usage():
    model = RuntimeStatusModel()

    model.update_tokens(
        prompt_tokens=10,
        completion_tokens=5,
        turn_total=15,
        session_total=100,
        context_usage=1.25,
        context_window=200_000,
    )

    assert model.snapshot().tokens == TokenRuntimeStatus(
        prompt_tokens=10,
        completion_tokens=5,
        turn_total=15,
        session_total=100,
        context_usage=1.0,
        context_window=200_000,
    )

    model.update_tokens(context_usage=-0.5)

    assert model.snapshot().tokens.context_usage == 0.0


def test_subagent_upsert_and_remove():
    model = RuntimeStatusModel()

    model.upsert_subagent(
        agent_id="agent-1",
        task_name="Summarize tool output",
        status="running",
        progress_pct=1.5,
        result_summary="",
        retry_count=1,
        max_retries=3,
        duration_ms=None,
    )

    assert model.snapshot().subagents == (
        SubAgentRuntimeInfo(
            agent_id="agent-1",
            task_name="Summarize tool output",
            status="running",
            progress_pct=1.0,
            result_summary="",
            retry_count=1,
            max_retries=3,
            duration_ms=None,
        ),
    )

    model.upsert_subagent(
        agent_id="agent-1",
        task_name="Summarize tool output",
        status="completed",
        progress_pct=0.75,
        result_summary="Summary ready",
        retry_count=2,
        max_retries=3,
        duration_ms=42.5,
    )

    assert model.snapshot().subagents == (
        SubAgentRuntimeInfo(
            agent_id="agent-1",
            task_name="Summarize tool output",
            status="completed",
            progress_pct=0.75,
            result_summary="Summary ready",
            retry_count=2,
            max_retries=3,
            duration_ms=42.5,
        ),
    )

    model.remove_subagent("agent-1")

    assert model.snapshot().subagents == ()


def test_goal_update_replaces_goal_status_and_allows_clearing_budget():
    model = RuntimeStatusModel()

    model.update_goal(
        name="Finish inspector pane",
        active=True,
        achieved=False,
        waiting_for_user=True,
        budget_used=120,
        budget_limit=500,
    )

    assert model.snapshot().goal == GoalRuntimeStatus(
        name="Finish inspector pane",
        active=True,
        achieved=False,
        waiting_for_user=True,
        budget_used=120,
        budget_limit=500,
    )

    model.update_goal(
        active=False,
        achieved=True,
        waiting_for_user=False,
        budget_used=None,
        budget_limit=None,
    )

    assert model.snapshot().goal == GoalRuntimeStatus(
        name="Finish inspector pane",
        active=False,
        achieved=True,
        waiting_for_user=False,
        budget_used=None,
        budget_limit=None,
    )


def test_tool_update_upserts_tool_status():
    model = RuntimeStatusModel()

    model.update_tool(
        name="shell_command",
        status="running",
        permission_waiting=True,
        last_result_summary="",
        duration_ms=None,
    )

    assert model.snapshot().tools == (
        ToolRuntimeStatus(
            name="shell_command",
            status="running",
            permission_waiting=True,
            last_result_summary="",
            duration_ms=None,
        ),
    )

    model.update_tool(
        name="shell_command",
        status="completed",
        permission_waiting=False,
        last_result_summary="exit code 0",
        duration_ms=8.25,
    )

    assert model.snapshot().tools == (
        ToolRuntimeStatus(
            name="shell_command",
            status="completed",
            permission_waiting=False,
            last_result_summary="exit code 0",
            duration_ms=8.25,
        ),
    )


def test_health_retry_update_allows_unknown_mcp_state():
    model = RuntimeStatusModel()

    model.update_health(
        retry_info="retry 2/3 after timeout",
        mcp_connected=True,
        last_error="timeout",
    )

    assert model.snapshot().health == HealthRuntimeStatus(
        retry_info="retry 2/3 after timeout",
        mcp_connected=True,
        last_error="timeout",
    )

    model.update_health(mcp_connected=None, last_error="")

    assert model.snapshot().health == HealthRuntimeStatus(
        retry_info="retry 2/3 after timeout",
        mcp_connected=None,
        last_error="",
    )


def test_snapshot_returns_immutable_subagent_tuple():
    model = RuntimeStatusModel()
    model.upsert_subagent(
        agent_id="agent-1",
        task_name="Inspect project",
        status="running",
        progress_pct=0.25,
        result_summary="",
        retry_count=0,
        max_retries=2,
        duration_ms=None,
    )

    first_snapshot = model.snapshot()
    assert isinstance(first_snapshot.subagents, tuple)

    with pytest.raises(AttributeError):
        first_snapshot.subagents.append(
            SubAgentRuntimeInfo(
                agent_id="agent-2",
                task_name="Injected by caller",
                status="running",
                progress_pct=0.5,
                result_summary="",
                retry_count=0,
                max_retries=2,
                duration_ms=None,
            )
        )

    assert model.snapshot().subagents == (
        SubAgentRuntimeInfo(
            agent_id="agent-1",
            task_name="Inspect project",
            status="running",
            progress_pct=0.25,
            result_summary="",
            retry_count=0,
            max_retries=2,
            duration_ms=None,
        ),
    )


def test_snapshot_returns_immutable_tool_tuple():
    model = RuntimeStatusModel()
    model.update_tool(
        name="shell_command",
        status="running",
        permission_waiting=True,
        last_result_summary="waiting for permission",
        duration_ms=None,
    )

    first_snapshot = model.snapshot()
    assert isinstance(first_snapshot.tools, tuple)

    with pytest.raises(AttributeError):
        first_snapshot.tools.append(
            ToolRuntimeStatus(
                name="injected",
                status="running",
                permission_waiting=False,
                last_result_summary="",
                duration_ms=None,
            )
        )

    assert model.snapshot().tools == (
        ToolRuntimeStatus(
            name="shell_command",
            status="running",
            permission_waiting=True,
            last_result_summary="waiting for permission",
            duration_ms=None,
        ),
    )


def test_clear_transient_clears_ephemeral_status_and_preserves_durable_state():
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-1",
        project_name="MyAgentCLI",
        model="deepseek-v4-pro",
        thinking="think-high",
    )
    model.update_tokens(
        prompt_tokens=10,
        completion_tokens=5,
        turn_total=15,
        session_total=100,
        context_usage=0.25,
        context_window=200_000,
    )
    model.update_goal(
        name="Ship inspector pane",
        active=True,
        achieved=False,
        waiting_for_user=True,
        budget_used=50,
        budget_limit=200,
    )
    model.update_tool(
        name="shell_command",
        status="running",
        permission_waiting=True,
        last_result_summary="waiting",
        duration_ms=None,
    )
    model.update_health(
        retry_info="retry 1/3 after timeout",
        mcp_connected=True,
        last_error="timeout",
    )

    model.clear_transient()

    snapshot = model.snapshot()
    assert snapshot.session == SessionRuntimeStatus(
        session_id="session-1",
        project_name="MyAgentCLI",
        model="deepseek-v4-pro",
        thinking="think-high",
    )
    assert snapshot.tokens == TokenRuntimeStatus(
        prompt_tokens=10,
        completion_tokens=5,
        turn_total=15,
        session_total=100,
        context_usage=0.25,
        context_window=200_000,
    )
    assert snapshot.goal == GoalRuntimeStatus(
        name="Ship inspector pane",
        active=True,
        achieved=False,
        waiting_for_user=False,
        budget_used=50,
        budget_limit=200,
    )
    assert snapshot.tools == ()
    assert snapshot.health == HealthRuntimeStatus(
        retry_info="",
        mcp_connected=True,
        last_error="",
    )
