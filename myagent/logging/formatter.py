"""JsonLineFormatter — outputs log records as single-line JSON.

Design doc reference: §十一 日志系统
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone


class JsonLineFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Each line contains: timestamp, level, logger name, message,
    pid, plus any extra fields from the log record and context vars.
    Exception info includes type, message, and full traceback.
    """

    # Standard LogRecord attributes — these are NOT custom extra fields
    _STD_RECORD_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "process", "processName", "message",
        "asctime", "taskName",
    })

    # Known spec-defined fields that should be extracted at top level
    _KNOWN_TOP_KEYS = frozenset({
        "category", "component", "context", "event", "subagent_id",
        "parent_session", "prompt_summary",
    })

    def format(self, record: logging.LogRecord) -> str:
        from myagent.logging.context import get_context

        log_dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "pid": os.getpid(),
        }

        # Context vars (session_id, project)
        ctx = get_context()
        if ctx["session_id"]:
            log_dict["session_id"] = ctx["session_id"]
        if ctx["project"]:
            log_dict["project"] = ctx["project"]

        # Collect extra fields: support both `extra_fields` dict AND
        # standard `extra={...}` whose keys become individual record attrs.
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            # Legacy pattern: extra={"extra_fields": {"category": "system", ...}}
            for key, value in extra_fields.items():
                if key in self._KNOWN_TOP_KEYS:
                    log_dict[key] = value
                log_dict[key] = value
        else:
            # Standard pattern: extra={"category": "llm", "event": "request", ...}
            # Python logging sets these as individual attributes on the record.
            for key, value in record.__dict__.items():
                if key in self._STD_RECORD_ATTRS:
                    continue
                if key.startswith("_"):
                    continue
                if key == "extra_fields":
                    continue
                if key in self._KNOWN_TOP_KEYS:
                    log_dict[key] = value
                log_dict[key] = value

        # Handle exception info
        if record.exc_info and record.exc_info[1]:
            exc_type = type(record.exc_info[1]).__name__
            exc_msg = str(record.exc_info[1])
            log_dict["exception_type"] = exc_type
            log_dict["exception_message"] = exc_msg
            # Include full traceback
            log_dict["traceback"] = traceback.format_exception(
                record.exc_info[0], record.exc_info[1], record.exc_info[2]
            )

        return json.dumps(log_dict, ensure_ascii=False, default=str)
