"""Tests for LogManager and get_logger."""

import json
import logging
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

        # Emit startup event with session_id (gap-18-04: now separate from setup)
        LogManager.log_startup(config=config, session_id="test-session-123")

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
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) >= 3

        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert "level" in data

        # Find our tool log
        tool_lines = [
            line for line in lines if '"tool_name"' in line and '"read"' in line
        ]
        assert len(tool_lines) == 1
        data = json.loads(tool_lines[0])
        assert data["tool_name"] == "read"
        assert data["duration_ms"] == 12

        # Clean up
        log_mod._initialized = False

    def test_shutdown_restores_myagent_logger_propagation(self, tmp_path, caplog):
        import myagent.logging.logger as log_mod
        from myagent.config.schema import LoggingConfig

        log_mod._initialized = False
        log_mod._queue_listener = None
        caplog.set_level(logging.ERROR, logger="myagent.memory.dream")

        LogManager.setup(
            config=LoggingConfig(
                level="DEBUG",
                dir=str(tmp_path / "logs"),
                format="jsonl",
            )
        )
        LogManager.shutdown()

        logging.getLogger("myagent.memory.dream").error("after shutdown")

        assert any(
            record.name == "myagent.memory.dream"
            and record.message == "after shutdown"
            for record in caplog.records
        )
        log_mod._initialized = False
