"""Tests for DreamEngine."""

import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from myagent.memory.dream import DreamEngine, DreamResult, TranscriptFindings
from myagent.memory.store import MemoryStore
from myagent.permissions.controller import PermissionController, PermissionResult
from myagent.tools.base import ToolResult
from myagent.tools.builtin.memory_tools import MemoryWriteTool


class TestDreamEngine:
    def test_should_run_conditions_met(self, tmp_path):
        from myagent.config.schema import DreamConfig

        config = DreamConfig(trigger_hours=0, trigger_rounds=10, enabled=True)
        engine = DreamEngine(config=config, state_dir=tmp_path)
        # No previous run, > trigger_rounds
        assert engine.should_run(session_rounds=50) is True

    def test_should_run_not_enough_rounds(self, tmp_path):
        from myagent.config.schema import DreamConfig

        config = DreamConfig(trigger_hours=6, trigger_rounds=50, enabled=True)
        engine = DreamEngine(config=config, state_dir=tmp_path)
        assert engine.should_run(session_rounds=10) is False

    def test_should_run_disabled(self, tmp_path):
        from myagent.config.schema import DreamConfig

        config = DreamConfig(enabled=False)
        engine = DreamEngine(config=config, state_dir=tmp_path)
        assert engine.should_run(session_rounds=100) is False

    @pytest.mark.asyncio
    async def test_run_creates_log(self, tmp_path):
        engine = DreamEngine(state_dir=tmp_path)
        result = await engine.run()
        assert result.log_path is not None
        assert result.log_path.exists()
        assert "Dream Log" in result.log_path.read_text()

    @pytest.mark.asyncio
    async def test_run_as_subagent_passes_memory_store_tool_context(self, tmp_path):
        project_dir = tmp_path / "project"
        memory_store = MemoryStore(
            project_memory_dir=project_dir / ".myagent" / "memory",
            user_memory_dir=tmp_path / "user-memory",
        )

        class FakeHandle:
            async def wait(self):
                return ToolResult(output="Memory actions completed")

        class FakePool:
            def __init__(self):
                self.kwargs = None
                self.tool_result = None

            async def spawn(self, **kwargs):
                self.kwargs = kwargs
                ctx = kwargs.get("tool_context")
                if ctx is not None:
                    content = (
                        "---\n"
                        "name: dream-note\n"
                        "description: Dream note\n"
                        "metadata: {}\n"
                        "---\n\n"
                        "Remember to route dream memory writes through MemoryStore.\n"
                    )
                    self.tool_result = await MemoryWriteTool().execute(
                        {
                            "file_path": str(
                                memory_store.project_dir / "dream-note.md"
                            ),
                            "content": content,
                        },
                        ctx,
                    )
                return FakeHandle()

        pool = FakePool()
        engine = DreamEngine(
            state_dir=tmp_path / "state",
            memory_store=memory_store,
            subagent_pool=pool,
        )

        result = await engine.run(
            session_store=SimpleNamespace(base_dir=tmp_path / "sessions")
        )

        assert pool.kwargs is not None
        ctx = pool.kwargs.get("tool_context")
        assert ctx is not None
        assert ctx.memory_store is memory_store
        assert pool.tool_result.error is None
        assert memory_store.get_session_writes().created == ["dream-note"]
        assert result.memories_created == 1
        assert (memory_store.project_dir / "MEMORY.md").exists()

    @pytest.mark.asyncio
    async def test_dream_permissions_allow_memory_write_without_confirm(
        self, tmp_path
    ):
        project_dir = tmp_path / "project"
        memory_store = MemoryStore(
            project_memory_dir=project_dir / ".myagent" / "memory",
            user_memory_dir=tmp_path / "user-memory",
        )

        class ConfirmTrackingPermissions(PermissionController):
            def __init__(self):
                super().__init__()
                self.confirm_calls = 0

            async def confirm(self, tool_name: str, params: dict | None = None):
                self.confirm_calls += 1
                return False

        base_permissions = ConfirmTrackingPermissions()
        engine = DreamEngine(
            state_dir=tmp_path / "state",
            memory_store=memory_store,
            permissions=base_permissions,
            project_dir=project_dir,
        )

        ctx = engine._build_subagent_tool_context()
        content = (
            "---\n"
            "name: dream-note\n"
            "description: Dream note\n"
            "metadata: {}\n"
            "---\n\n"
            "Dream can update memories silently.\n"
        )
        params = {
            "file_path": str(memory_store.project_dir / "dream-note.md"),
            "content": content,
        }

        assert base_permissions.check("memory_write", level=1, params=params) == (
            PermissionResult.ASK
        )
        assert ctx.permissions.check("memory_write", level=1, params=params) == (
            PermissionResult.ALLOW
        )
        assert base_permissions.confirm_calls == 0

        tool_result = await MemoryWriteTool().execute(params, ctx)

        assert tool_result.error is None
        assert base_permissions.confirm_calls == 0
        assert memory_store.get_session_writes().created == ["dream-note"]
        assert (memory_store.project_dir / "MEMORY.md").exists()

    def test_dream_permissions_do_not_silently_allow_side_effect_tools(
        self, tmp_path
    ):
        project_dir = tmp_path / "project"
        memory_store = MemoryStore(
            project_memory_dir=project_dir / ".myagent" / "memory",
            user_memory_dir=tmp_path / "user-memory",
        )
        engine = DreamEngine(
            state_dir=tmp_path / "state",
            memory_store=memory_store,
            permissions=PermissionController(default_mode="allow_all"),
            project_dir=project_dir,
        )

        ctx = engine._build_subagent_tool_context()

        assert ctx.permissions.check(
            "write",
            level=1,
            params={"file_path": str(project_dir / "code.py"), "content": "x"},
        ) == PermissionResult.DENY
        assert ctx.permissions.check(
            "web_fetch",
            level=0,
            params={"url": "https://example.com"},
        ) == PermissionResult.DENY
        assert ctx.permissions.check(
            "mcp_external_tool",
            level=3,
            params={},
        ) == PermissionResult.DENY

    def test_dream_subagent_prompt_renders_json_summary_template(self, tmp_path):
        engine = DreamEngine(state_dir=tmp_path)

        prompt = engine._build_dream_subagent_prompt(
            "No memories",
            "No transcripts",
        )

        assert '"created"' in prompt
        assert '"updated"' in prompt
        assert '"deleted"' in prompt

    @pytest.mark.asyncio
    async def test_scan_transcripts_includes_old_unprocessed_transcript(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        transcript = (
            sessions_dir / "project" / "hash" / "old-session" / "transcript.json"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps({
                "session_id": "old-session",
                "messages": [
                    {"role": "user", "content": "Actually, use pytest."},
                ],
            }),
            encoding="utf-8",
        )
        old_mtime = time.time() - 30 * 86400
        os.utime(transcript, (old_mtime, old_mtime))

        engine = DreamEngine(state_dir=tmp_path / "state")
        findings = await engine._scan_transcripts(
            SimpleNamespace(base_dir=sessions_dir)
        )

        assert findings.sessions_scanned == 1
        assert findings.correction_count == 1

    @pytest.mark.asyncio
    async def test_scan_transcripts_skips_processed_transcript(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        transcript = (
            sessions_dir / "project" / "hash" / "processed-session" / "transcript.json"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps({
                "session_id": "processed-session",
                "messages": [
                    {"role": "user", "content": "Actually, correction: use pytest."},
                ],
            }),
            encoding="utf-8",
        )

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "last_dream.json").write_text(
            json.dumps({"processed_transcripts": [str(transcript.resolve())]}),
            encoding="utf-8",
        )

        engine = DreamEngine(state_dir=state_dir)
        findings = await engine._scan_transcripts(
            SimpleNamespace(base_dir=sessions_dir)
        )

        assert findings.sessions_scanned == 0
        assert findings.correction_count == 0

    @pytest.mark.asyncio
    async def test_create_memory_failure_logs_structured_error(self, tmp_path, caplog):
        class FailingMemoryStore:
            project_dir = tmp_path

            async def write(self, file_path: str, content: str):
                raise RuntimeError("memory write failed")

        engine = DreamEngine(state_dir=tmp_path / "state", memory_store=FailingMemoryStore())
        findings = TranscriptFindings(
            correction_count=2,
            correction_markers=["Actually"],
            sessions_scanned=1,
            text=["corrections detected"],
        )
        caplog.set_level(logging.ERROR, logger="myagent.memory.dream")

        await engine._create_memories_from_patterns(findings, DreamResult(), [], [])

        record = next(record for record in caplog.records if record.name == "myagent.memory.dream")
        assert record.category == "error"
        assert record.component == "agent"
        assert record.context == "dream.create_common_corrections_memory"
        assert record.exc_info is not None
