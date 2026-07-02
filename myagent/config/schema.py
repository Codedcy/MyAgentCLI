"""Configuration schema — all config types with defaults.

Design doc reference: §九 — 配置系统
"""

from dataclasses import dataclass, field
from typing import Literal


# ── Nested config types ──────────────────────────────────────────


@dataclass
class CompressionConfig:
    primary_threshold: float = 0.75
    target_after: float = 0.30
    hard_limit: float = 0.90
    minimum_messages: int = 10
    minimum_savings: float = 0.10


@dataclass
class ContextConfig:
    compression: CompressionConfig = field(default_factory=CompressionConfig)


@dataclass
class AutoAllowConfig:
    levels: list[int] = field(default_factory=lambda: [0])
    paths: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)


@dataclass
class AutoDenyConfig:
    paths: list[str] = field(default_factory=lambda: [".env", "*.key", "*.pem"])
    commands: list[str] = field(default_factory=lambda: ["sudo", "rm -rf /"])


@dataclass
class PermissionsConfig:
    default_mode: Literal["ask", "allow_all"] = "ask"
    auto_allow: AutoAllowConfig = field(default_factory=AutoAllowConfig)
    auto_deny: AutoDenyConfig = field(default_factory=AutoDenyConfig)


@dataclass
class SubagentsConfig:
    max_concurrent: int = 10
    speculative_exploration: bool = False


@dataclass
class DreamConfig:
    trigger_hours: int = 6
    trigger_rounds: int = 50
    enabled: bool = True


@dataclass
class ToolsConfig:
    tool_result_max_chars: int = 5000
    shell_timeout_seconds: int = 120


@dataclass
class UIConfig:
    show_status_bar: bool = True
    status_bar_items: list[str] = field(
        default_factory=lambda: ["subagents", "tokens", "thinking"]
    )
    streaming: bool = True
    syntax_highlight: bool = True


@dataclass
class SessionConfig:
    save_transcripts: bool = True
    transcript_format: list[str] = field(
        default_factory=lambda: ["json", "markdown"]
    )
    sessions_dir: str = "~/.myagent/sessions/"


@dataclass
class LoggingConfig:
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: str = "~/.myagent/logs/"
    format: Literal["jsonl", "text", "both"] = "jsonl"
    max_size_mb: int = 100
    retention_days: int = 30
    llm_prompts: bool = False


@dataclass
class ModelConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    thinking: Literal["Think High", "Think Max", "Non-think"] = "Think High"
    fallback_models: list[str] = field(default_factory=list)


# ── Top-level config ─────────────────────────────────────────────


@dataclass
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    subagents: SubagentsConfig = field(default_factory=SubagentsConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
