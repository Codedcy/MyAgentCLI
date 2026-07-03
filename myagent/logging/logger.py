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


class TimedSizeRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Combined time-based (midnight) + size-based file rotation handler.

    Design doc §十一 specifies: "日志文件通过 TimedRotatingFileHandler 按天轮转
    + RotatingFileHandler 按大小轮转组合". This class subclasses
    TimedRotatingFileHandler (the primary rotation axis is time — daily at
    midnight) and adds size-based rotation within each day by checking
    file size on each emit and performing a numbered rollover when the
    size exceeds max_bytes (gap-15-03).
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int = 0,
        backup_count: int = 0,
        encoding: str = "utf-8",
        delay: bool = True,
    ):
        # TimedRotatingFileHandler init: daily rotation at midnight,
        # keep backup_count days of old log files.
        super().__init__(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            delay=delay,
            utc=False,
        )
        self.max_bytes = max_bytes
        # Suffix for size-based rotation within a day (e.g. .1, .2, ...)
        self._size_suffix = ".%d"

    def emit(self, record: logging.LogRecord) -> None:
        """Check file size before emitting. If max_bytes is exceeded,
        perform a size-based rollover (numbered backup) within the
        current day. Time-based (midnight) rotation is handled by the
        parent TimedRotatingFileHandler.
        """
        if self.max_bytes > 0 and self.stream is not None:
            try:
                current_size = self.stream.tell()
                if current_size >= self.max_bytes:
                    self._do_size_rollover()
            except (OSError, ValueError, AttributeError):
                # If we can't determine the size, just proceed
                pass
        super().emit(record)

    def _do_size_rollover(self) -> None:
        """Perform a size-based rollover: rename files with .1, .2, ...
        suffixes (matching RotatingFileHandler behavior), then open a new
        file. The parent's midnight rotation handles the daily boundary.
        """
        if self.stream:
            self.stream.close()
            self.stream = None

        # Rotate existing numbered backup files: .2 → .3, .1 → .2
        for i in range(self.backupCount - 1, 0, -1):
            sfn = self.rotation_filename(f"{self.baseFilename}{self._size_suffix % i}")
            dfn = self.rotation_filename(f"{self.baseFilename}{self._size_suffix % (i + 1)}")
            if os.path.exists(sfn):
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)

        # Rename current file to .1
        dfn = self.rotation_filename(f"{self.baseFilename}{self._size_suffix % 1}")
        if os.path.exists(self.baseFilename):
            if os.path.exists(dfn):
                os.remove(dfn)
            os.rename(self.baseFilename, dfn)

        # Open a new empty file
        if not self.delay:
            self.stream = self._open()


class LogManager:
    """Manages logging lifecycle: setup, rotation, cleanup, shutdown."""

    @staticmethod
    def setup(config=None, session_id: str | None = None) -> None:
        """Initialize the logging tree.

        Called once at application startup. Creates:
        - Root logger "myagent" with configured level
        - QueueHandler -> QueueListener -> file handlers
        - Daily rotation + size-based rotation + retention cleanup

        Args:
            config: LoggingConfig dataclass. If None, uses defaults.
            session_id: Optional session ID for context. If provided, sets
                context before the startup event is emitted (gap-18-04).
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

        # Infrastructure is ready. The startup event is emitted separately
        # via log_startup() after session creation, so it always carries
        # the session_id (gap-18-04).

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
    def log_startup(config=None, session_id: str | None = None) -> None:
        """Log a startup event with session_id in context (gap-18-04).

        Call this AFTER session creation when session_id becomes known.
        The startup event will carry the session_id via LogContext.

        Args:
            config: LoggingConfig dataclass. If None, uses defaults.
            session_id: Session ID to bind to logging context before emitting.
        """
        root = logging.getLogger("myagent")
        if session_id:
            from myagent.logging.context import set_context
            set_context(session_id=session_id)
        if config is None:
            from myagent.config.schema import LoggingConfig
            config = LoggingConfig()
        LogManager._emit_startup_event(root, config)

    @staticmethod
    def _emit_startup_event(root: logging.Logger, config: object) -> None:
        """Emit a single startup event with metadata (gap-2-12, gap-18-04)."""
        import hashlib, json, platform, sys
        config_hash = ""
        python_version = sys.version
        platform_info = platform.platform()
        try:
            if config and hasattr(config, '__dict__'):
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
        """Create a TimedSizeRotatingFileHandler with time + size rotation.

        Subclasses TimedRotatingFileHandler (daily midnight rotation) and
        adds size-based rotation within each day. This matches the design
        doc §十一: "TimedRotatingFileHandler 按天轮转 + RotatingFileHandler
        按大小轮转组合" (gap-15-03).
        """
        return TimedSizeRotatingFileHandler(
            filename=filename,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )

