"""Logging system — async-safe JSON Lines logging.

Provides:
- LogManager.setup() / shutdown() — lifecycle
- get_logger(name) — convenience accessor
- JsonLineFormatter — single-line JSON output
- LogContext — session/project binding via contextvars
- Category constants: LOG_SYSTEM, LOG_LLM, LOG_TOOL, LOG_AGENT, LOG_SUBAGENT, LOG_ERROR

Design doc reference: §十一 日志系统
"""

from myagent.logging.context import clear_context, get_context, set_context
from myagent.logging.formatter import JsonLineFormatter
from myagent.logging.logger import (
    LOG_AGENT,
    LOG_ERROR,
    LOG_LLM,
    LOG_SUBAGENT,
    LOG_SYSTEM,
    LOG_TOOL,
    LogManager,
    get_logger,
)

__all__ = [
    "clear_context",
    "get_context",
    "get_logger",
    "JsonLineFormatter",
    "LOG_AGENT",
    "LOG_ERROR",
    "LOG_LLM",
    "LOG_SUBAGENT",
    "LOG_SYSTEM",
    "LOG_TOOL",
    "LogManager",
    "set_context",
]
