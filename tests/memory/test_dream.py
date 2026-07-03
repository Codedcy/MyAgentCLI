"""Tests for DreamEngine."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from myagent.memory.dream import DreamEngine


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
