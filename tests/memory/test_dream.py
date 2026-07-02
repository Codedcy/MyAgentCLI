"""Tests for DreamEngine."""

import time
from pathlib import Path

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
