"""Tests for JsonLineFormatter."""

import json
import logging
import sys

from myagent.logging.formatter import JsonLineFormatter


class TestJsonLineFormatter:
    def test_outputs_valid_json(self):
        formatter = JsonLineFormatter()
        record = logging.LogRecord(
            name="myagent.tools",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "myagent.tools"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_includes_extra_fields(self):
        formatter = JsonLineFormatter()
        record = logging.LogRecord(
            name="myagent.llm",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="LLM request",
            args=(),
            exc_info=None,
        )
        extra = {
            "category": "llm",
            "event": "request",
            "model": "deepseek-v4-pro",
            "messages_count": 10,
        }
        # Simulate how extra fields are attached
        record.extra_fields = extra
        output = formatter.format(record)
        data = json.loads(output)
        assert data["category"] == "llm"
        assert data["model"] == "deepseek-v4-pro"
        assert data["messages_count"] == 10

    def test_missing_extra_fields_graceful(self):
        formatter = JsonLineFormatter()
        record = logging.LogRecord(
            name="myagent.tools",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="Debug info",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "DEBUG"
        # Should not have extra fields
        assert "category" not in data

    def test_error_record_includes_exception_metadata(self):
        formatter = JsonLineFormatter()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = logging.LogRecord(
                name="myagent.tools",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="Tool failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        record.category = "error"
        record.component = "tool"
        record.context = "memory_write"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["category"] == "error"
        assert data["component"] == "tool"
        assert data["context"] == "memory_write"
        assert data["exception_type"] == "RuntimeError"
        assert "RuntimeError: boom" in "".join(data["traceback"])
