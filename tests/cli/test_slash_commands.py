"""Tests for slash commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from myagent.agent.prompt_capture import LastPromptCapture
from myagent.cli.commands import CommandContext, CommandDispatcher
from myagent.memory.dream import DreamResult


class TestCommandDispatcher:
    @pytest.mark.asyncio
    async def test_mode_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(config=None)
        result = await dispatcher.dispatch("/mode think-max", ctx)
        assert "Think Max" in result.output

    @pytest.mark.asyncio
    async def test_goal_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(goal_tracker=None)
        result = await dispatcher.dispatch("/goal", ctx)
        assert "None" in result.output

    @pytest.mark.asyncio
    async def test_goal_set(self):
        from myagent.agent.goal import GoalTracker
        gt = GoalTracker()
        dispatcher = CommandDispatcher()
        ctx = CommandContext(goal_tracker=gt)
        result = await dispatcher.dispatch("/goal Fix all bugs", ctx)
        assert "Fix all bugs" in result.output
        assert gt.get_goal() == "Fix all bugs"

    @pytest.mark.asyncio
    async def test_exit(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("/exit", ctx)
        assert "Goodbye" in result.output

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("/nonexistent", ctx)
        assert not result.success

    @pytest.mark.asyncio
    async def test_not_a_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("not a slash command", ctx)
        assert not result.success

    @pytest.mark.asyncio
    async def test_dream_command_passes_active_session_store(self, tmp_path):
        dispatcher = CommandDispatcher()
        session_store = object()
        dream_engine = SimpleNamespace(
            run=AsyncMock(return_value=DreamResult(log_path=tmp_path / "dream.md"))
        )
        ctx = CommandContext(
            dream_engine=dream_engine,
            session_manager=SimpleNamespace(session_store=session_store),
        )

        result = await dispatcher.dispatch("/dream", ctx)

        assert result.success
        dream_engine.run.assert_awaited_once_with(session_store=session_store)

    @pytest.mark.asyncio
    async def test_prompt_command_reports_missing_capture(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(engine=SimpleNamespace(get_last_prompt_capture=lambda: None))

        result = await dispatcher.dispatch("/prompt", ctx)

        assert result.success
        assert result.output == "No LLM prompt captured yet."

    @pytest.mark.asyncio
    async def test_prompt_command_renders_readable_capture(self):
        capture = LastPromptCapture.capture(
            model="deepseek-v4-pro",
            thinking="Think High",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            captured_at="2026-07-08T12:34:56+00:00",
        )
        dispatcher = CommandDispatcher()
        ctx = CommandContext(engine=SimpleNamespace(get_last_prompt_capture=lambda: capture))

        result = await dispatcher.dispatch("/prompt", ctx)

        assert result.success
        assert "Last LLM prompt" in result.output
        assert "deepseek-v4-pro" in result.output
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_prompt_command_renders_raw_json(self):
        capture = LastPromptCapture.capture(
            model="deepseek-v4-pro",
            thinking="Think High",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            captured_at="2026-07-08T12:34:56+00:00",
        )
        dispatcher = CommandDispatcher()
        ctx = CommandContext(engine=SimpleNamespace(get_last_prompt_capture=lambda: capture))

        result = await dispatcher.dispatch("/prompt raw", ctx)

        assert result.success
        assert '"model": "deepseek-v4-pro"' in result.output
        assert '"messages"' in result.output

    @pytest.mark.asyncio
    async def test_prompt_command_rejects_unknown_argument(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(engine=SimpleNamespace(get_last_prompt_capture=lambda: None))

        result = await dispatcher.dispatch("/prompt verbose", ctx)

        assert not result.success
        assert result.output == "Usage: /prompt [raw]"

    @pytest.mark.asyncio
    async def test_init_command_creates_project_guidance_file(self, tmp_path):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(project_dir=tmp_path)

        result = await dispatcher.dispatch("/init", ctx)

        assert result.success
        assert "Created:" in result.output
        assert (tmp_path / "AGENTS.md").is_file()

    @pytest.mark.asyncio
    async def test_init_command_preserves_existing_file_without_force(self, tmp_path):
        guidance = tmp_path / "AGENTS.md"
        guidance.write_text("custom guidance", encoding="utf-8")
        dispatcher = CommandDispatcher()
        ctx = CommandContext(project_dir=tmp_path)

        result = await dispatcher.dispatch("/init", ctx)

        assert result.success
        assert "Already exists:" in result.output
        assert guidance.read_text(encoding="utf-8") == "custom guidance"

    @pytest.mark.asyncio
    async def test_init_command_force_overwrites_existing_file(self, tmp_path):
        guidance = tmp_path / "AGENTS.md"
        guidance.write_text("custom guidance", encoding="utf-8")
        dispatcher = CommandDispatcher()
        ctx = CommandContext(project_dir=tmp_path)

        result = await dispatcher.dispatch("/init --force", ctx)

        assert result.success
        assert "Created:" in result.output
        assert "Project Overview" in guidance.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_help_lists_prompt_command(self):
        dispatcher = CommandDispatcher()

        result = await dispatcher.dispatch("/help", CommandContext())

        assert result.success
        assert "/prompt [raw]" in result.output
        assert "/init [--force]" in result.output

    @pytest.mark.asyncio
    async def test_subagents_command_lists_pool_state_and_transcript_path(self):
        dispatcher = CommandDispatcher()
        pool = SimpleNamespace(
            list_subagents=lambda: [
                {
                    "id": "sub-001",
                    "status": "completed",
                    "task_name": "Draft PRD",
                    "summary": "PRD complete",
                    "transcript_path": "sessions/subagents/sub-001/transcript.md",
                }
            ]
        )
        ctx = CommandContext(subagent_pool=pool)

        result = await dispatcher.dispatch("/subagents", ctx)

        assert result.success
        assert "sub-001" in result.output
        assert "completed" in result.output
        assert "Draft PRD" in result.output
        assert "sessions/subagents/sub-001/transcript.md" in result.output

    @pytest.mark.asyncio
    async def test_subagent_command_shows_single_output(self):
        dispatcher = CommandDispatcher()
        pool = SimpleNamespace(
            get_subagent_output=lambda agent_id: {
                "id": agent_id,
                "status": "completed",
                "output": "Full sub-agent result",
                "transcript_path": "sessions/subagents/sub-001/transcript.md",
            }
        )
        ctx = CommandContext(subagent_pool=pool)

        result = await dispatcher.dispatch("/subagent sub-001", ctx)

        assert result.success
        assert "sub-001" in result.output
        assert "Full sub-agent result" in result.output
        assert "transcript.md" in result.output
