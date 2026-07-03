"""LogManager — setup, rotation, cleanup, and shutdown.

Async-safe logging via QueueHandler + QueueListener.
All log calls enqueue to a single background thread.

Design doc reference: §十一 日志系统
"""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import time
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from myagent.logging.formatter import JsonLineFormatter

if TYPE_CHECKING:
    from myagent.config.schema import LoggingConfig

# Log category constants
LOG_SYSTEM = "system"
LOG_LLM = "llm"
LOG_TOOL = "tool"
LOG_AGENT = "agent"
LOG_SUBAGENT = "subagent"
LOG_ERROR = "error"

_initialized = False
_queue_listener: logging.handlers.QueueListener | None = None
_root_logger: logging.Logger | None = None


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the 'myagent' namespace.

    Equivalent to logging.getLogger(f"myagent.{name}").
    """
    return logging.getLogger(f"myagent.{name}")


class LogManager:
    """Manages logging lifecycle: setup, rotation, cleanup, shutdown."""

    @staticmethod
    def setup(config=None, session_id: str | None = None) -> None:
        """Initialize the logging tree.

        Called once at application startup. Creates:
        - Root logger "myagent" with configured level
        - QueueHandler → QueueListener → file handlers
        - Daily rotation + size-based rotation + retention cleanup

        Args:
            config: LoggingConfig dataclass. If None, uses defaults.
            session_id: Optional session ID for context.
        """
        global _initialized, _queue_listener, _root_logger

        if _initialized:
            return

        # Resolve config
        if config is None:
            from myagent.config.schema import LoggingConfig

            config = LoggingConfig()

        # Resolve log directory
        log_dir = Path(config.dir).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create root logger
        root = logging.getLogger("myagent")
        root.setLevel(getattr(logging, config.level, logging.INFO))
        root.handlers.clear()
        root.propagate = False

        # Resolve size rotation max_bytes
        max_bytes = config.max_size_mb * 1024 * 1024
        size_backup_count = max(config.retention_days, 5)

        # Date stamp for daily rotation (gap-09)
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Build handlers
        handlers = []

        if config.format in ("jsonl", "both"):
            jsonl_path = str(log_dir / f"myagent-{date_str}.jsonl")
            jsonl_handler = LogManager._make_rotating_handler(
                jsonl_path, max_bytes, size_backup_count
            )
            jsonl_handler.setFormatter(JsonLineFormatter())
            jsonl_handler.setLevel(logging.DEBUG)
            handlers.append(jsonl_handler)

        if config.format in ("text", "both"):
            text_path = str(log_dir / f"myagent-{date_str}.log")
            text_handler = LogManager._make_rotating_handler(
                text_path, max_bytes, size_backup_count
            )
            text_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                )
            )
            text_handler.setLevel(logging.DEBUG)
            handlers.append(text_handler)

        # Queue handler (async-safe)
        queue = logging.handlers.QueueHandler(Queue(-1))
        root.addHandler(queue)

        # Queue listener (background thread)
        _queue_listener = logging.handlers.QueueListener(
            queue.queue, *handlers, respect_handler_level=True
        )
        _queue_listener.start()

        # Clean up old logs
        LogManager._cleanup_old_logs(log_dir, config.retention_days)

        # Register atexit
        atexit.register(LogManager.shutdown)

        _root_logger = root
        _initialized = True

        # Log startup
        if session_id:
            from myagent.logging.context import set_context
            set_context(session_id=session_id)

        root.info(
            "Logging initialized",
            extra={"extra_fields": {"category": LOG_SYSTEM, "event": "startup"}},
        )

    @staticmethod
    def shutdown() -> None:
        """Flush queue, stop listener, close handlers. Called at exit."""
        global _initialized, _queue_listener

        if not _initialized:
            return

        root = logging.getLogger("myagent")
        root.info(
            "Logging shutting down",
            extra={"extra_fields": {"category": LOG_SYSTEM, "event": "shutdown"}},
        )

        if _queue_listener:
            _queue_listener.stop()
            _queue_listener = None

        _initialized = False

    @staticmethod
    def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
        """Remove log files older than retention_days."""
        cutoff = time.time() - (retention_days * 86400)
        try:
            for f in log_dir.glob("myagent*.log*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
        except Exception:
            pass  # cleanup is best-effort

    @staticmethod
    def _make_rotating_handler(
        filename: str, max_bytes: int, backup_count: int
    ) -> logging.handlers.RotatingFileHandler:
        """Create a RotatingFileHandler with size-based rotation.

        Rotates when the file reaches max_bytes, keeping up to backup_count
        old files.
        """
        return logging.handlers.RotatingFileHandler(
            filename=filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    @staticmethod
    def _rotated_filename(default_name: str) -> str:
        """Insert date suffix for rotated files."""
        from datetime import datetime

        base = default_name.rsplit(".", 1)
        date_str = datetime.now().strftime("%Y-%m-%d")
        if len(base) == 2:
            return f"{base[0]}-{date_str}.{base[1]}"
        return f"{default_name}-{date_str}"
