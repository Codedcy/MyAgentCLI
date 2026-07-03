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
from datetime import datetime
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


class TimedSizeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Combined time-based (midnight) + size-based file rotation handler.

    Design doc §十一 specifies TimedRotatingFileHandler (daily) +
    RotatingFileHandler (size). This class subclasses RotatingFileHandler
    and adds midnight rollover by checking the date on each emit and
    recomputing the filename pattern when the day changes (gap-09).
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int = 0,
        backup_count: int = 0,
        encoding: str = "utf-8",
        delay: bool = True,
    ):
        self._base_pattern = filename
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._encoding = encoding
        self._delay = delay
        self._current_date = datetime.now().strftime("%Y-%m-%d")
        # Compute the actual filename with today's date
        self._last_base = ""
        actual_filename = self._compute_filename()
        super().__init__(
            filename=actual_filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
            delay=delay,
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Check for date change before emitting. If midnight has passed,
        close the current file and open a new one with the new date.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            new_filename = self._compute_filename()
            self._do_date_rollover(new_filename)
        super().emit(record)

    def _compute_filename(self) -> str:
        """Substitute the date placeholder in the base pattern."""
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        # Replace date in the filename if it follows the pattern myagent-YYYY-MM-DD
        import re
        pattern = r"myagent-\d{4}-\d{2}-\d{2}"
        if re.search(pattern, self._base_pattern):
            self._last_base = re.sub(pattern, f"myagent-{today}", self._base_pattern)
            return self._last_base
        # Fallback: insert date before extension
        if "." in self._base_pattern:
            base, ext = self._base_pattern.rsplit(".", 1)
            self._last_base = f"{base}-{today}.{ext}"
        else:
            self._last_base = f"{self._base_pattern}-{today}"
        return self._last_base

    def _do_date_rollover(self, new_filename: str) -> None:
        """Close current file stream and reopen with the new date-based name."""
        try:
            if self.stream:
                self.stream.close()
        except Exception:
            pass
        self.baseFilename = new_filename
        self.stream = self._open()


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

        # Log startup with metadata (gap-2-12)
        if session_id:
            from myagent.logging.context import set_context
            set_context(session_id=session_id)

        # Compute startup metadata
        import hashlib, json, platform, sys
        config_hash = ""
        python_version = sys.version
        platform_info = platform.platform()
        try:
            if config and hasattr(config, '__dict__'):
                # Use a stable serialization for hashing
                config_dict = {
                    k: v for k, v in config.__dict__.items()
                    if not k.startswith('_')
                }
                config_hash = hashlib.sha256(
                    json.dumps(config_dict, sort_keys=True, default=str).encode()
                ).hexdigest()[:12]
        except Exception:
            config_hash = "unknown"

        root.info(
            "Logging initialized",
            extra={
                "category": LOG_SYSTEM,
                "event": "startup",
                "config_hash": config_hash,
                "python_version": python_version,
                "platform": platform_info,
            },
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
            extra={"category": LOG_SYSTEM, "event": "shutdown"},
        )

        if _queue_listener:
            _queue_listener.stop()
            _queue_listener = None

        _initialized = False

    @staticmethod
    def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
        """Remove log files older than retention_days.

        Cleans up both .jsonl and .log files (and their rotated siblings).
        (gap-8-04: previously only matched myagent*.log*, missing .jsonl files)
        """
        cutoff = time.time() - (retention_days * 86400)
        try:
            for f in log_dir.glob("myagent*.*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
        except Exception:
            pass  # cleanup is best-effort

    @staticmethod
    def _make_rotating_handler(
        filename: str, max_bytes: int, backup_count: int
    ) -> "TimedSizeRotatingFileHandler":
        """Create a TimedSizeRotatingFileHandler with time + size-based rotation.

        Combines TimedRotatingFileHandler (midnight rollover) with
        RotatingFileHandler (size-based rollover) as specified in the
        design doc §十一 日志系统 (gap-09).
        """
        return TimedSizeRotatingFileHandler(
            filename=filename,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )

