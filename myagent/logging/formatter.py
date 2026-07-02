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

        # Include extra fields from the log record
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            # Extract known audit fields at top level for easier querying
            for key in ("category", "component", "context"):
                if key in extra_fields:
                    log_dict[key] = extra_fields[key]
            # Merge all extra fields
            log_dict.update(extra_fields)

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
