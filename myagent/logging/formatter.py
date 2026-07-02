"""JsonLineFormatter — outputs log records as single-line JSON.

Design doc reference: §十一 日志系统
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonLineFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Each line contains: timestamp, level, logger name, message,
    plus any extra fields from the log record and context vars.
    """

    def format(self, record: logging.LogRecord) -> str:
        from myagent.logging.context import get_context

        log_dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Context vars (session_id, project)
        ctx = get_context()
        if ctx["session_id"]:
            log_dict["session_id"] = ctx["session_id"]
        if ctx["project"]:
            log_dict["project"] = ctx["project"]

        # Include extra fields from the log record
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            log_dict.update(extra_fields)

        # Handle exception info
        if record.exc_info and record.exc_info[1]:
            log_dict["exception_type"] = type(record.exc_info[1]).__name__
            log_dict["exception_message"] = str(record.exc_info[1])

        return json.dumps(log_dict, ensure_ascii=False, default=str)
