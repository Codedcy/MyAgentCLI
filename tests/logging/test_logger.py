"""Tests for LogManager and get_logger."""

import json
import time

from myagent.logging.logger import LogManager, get_logger


class TestGetLogger:
    def test_returns_logger_under_myagent(self):
        logger = get_logger("tools.registry")
        assert logger.name == "myagent.tools.registry"


class TestLogManager:
    def test_setup_and_shutdown(self, tmp_path):
        """Full setup → log → shutdown lifecycle."""
        from myagent.config.schema import LoggingConfig

        log_dir = tmp_path / "logs"
        config = LoggingConfig(
            level="DEBUG",
            dir=str(log_dir),
            format="jsonl",
            retention_days=7,
        )

        # Need to create a fresh state
        import myagent.logging.logger as log_mod

        log_mod._initialized = False
        log_mod._queue_listener = None

        LogManager.setup(config=config, session_id="test-session-123")

        # Write a log message
        logger = get_logger("tools.registry")
        logger.info(
            "Tool executed: read",
            extra={
                "extra_fields": {
                    "category": "tool",
                    "tool_name": "read",
                    "duration_ms": 12,
                }
            },
        )

        # Shutdown
        time.sleep(0.3)  # give queue listener time to process
        LogManager.shutdown()

        # Verify log file exists and has valid JSONL
        log_files = list(log_dir.glob("myagent*"))
        assert len(log_files) > 0

        content = log_files[0].read_text(encoding="utf-8")
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) >= 3

        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert "level" in data

        # Find our tool log
        tool_lines = [l for l in lines if '"tool_name"' in l and '"read"' in l]
        assert len(tool_lines) == 1
        data = json.loads(tool_lines[0])
        assert data["tool_name"] == "read"
        assert data["duration_ms"] == 12

        # Clean up
        log_mod._initialized = False
