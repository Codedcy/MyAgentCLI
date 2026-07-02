"""Tests for config schema dataclasses."""

from myagent.config.schema import (
    AppConfig,
    AutoAllowConfig,
    AutoDenyConfig,
    CompressionConfig,
    ContextConfig,
    DreamConfig,
    LoggingConfig,
    ModelConfig,
    PermissionsConfig,
    SessionConfig,
    SubagentsConfig,
    ToolsConfig,
    UIConfig,
)


class TestModelConfig:
    def test_defaults(self):
        c = ModelConfig()
        assert c.provider == "deepseek"
        assert c.model == "deepseek-v4-pro"
        assert c.thinking == "Think High"
        assert c.fallback_models == []

    def test_custom(self):
        c = ModelConfig(
            provider="openai",
            model="gpt-4",
            thinking="Non-think",
            fallback_models=["deepseek-v4-pro"],
        )
        assert c.provider == "openai"
        assert c.model == "gpt-4"
        assert c.thinking == "Non-think"
        assert c.fallback_models == ["deepseek-v4-pro"]


class TestContextConfig:
    def test_defaults(self):
        c = ContextConfig()
        assert c.compression.primary_threshold == 0.75
        assert c.compression.target_after == 0.30
        assert c.compression.hard_limit == 0.90
        assert c.compression.minimum_messages == 10
        assert c.compression.minimum_savings == 0.10


class TestPermissionsConfig:
    def test_defaults(self):
        c = PermissionsConfig()
        assert c.default_mode == "ask"
        assert 0 in c.auto_allow.levels
        assert ".env" in c.auto_deny.paths
        assert "sudo" in c.auto_deny.commands


class TestLoggingConfig:
    def test_defaults(self):
        c = LoggingConfig()
        assert c.level == "INFO"
        assert c.format == "jsonl"
        assert c.max_size_mb == 100
        assert c.retention_days == 30
        assert c.llm_prompts is False


class TestSubagentsConfig:
    def test_defaults(self):
        c = SubagentsConfig()
        assert c.max_concurrent == 10
        assert c.speculative_exploration is False


class TestDreamConfig:
    def test_defaults(self):
        c = DreamConfig()
        assert c.trigger_hours == 6
        assert c.trigger_rounds == 50
        assert c.enabled is True


class TestToolsConfig:
    def test_defaults(self):
        c = ToolsConfig()
        assert c.tool_result_max_chars == 5000
        assert c.shell_timeout_seconds == 120


class TestUIConfig:
    def test_defaults(self):
        c = UIConfig()
        assert c.show_status_bar is True
        assert "subagents" in c.status_bar_items
        assert c.streaming is True
        assert c.syntax_highlight is True


class TestSessionConfig:
    def test_defaults(self):
        c = SessionConfig()
        assert c.save_transcripts is True
        assert "json" in c.transcript_format
        assert "markdown" in c.transcript_format


class TestAppConfig:
    def test_all_defaults(self):
        c = AppConfig()
        assert c.model.provider == "deepseek"
        assert c.model.thinking == "Think High"
        assert c.context.compression.primary_threshold == 0.75
        assert c.permissions.default_mode == "ask"
        assert c.subagents.max_concurrent == 10
        assert c.dream.enabled is True
        assert c.tools.tool_result_max_chars == 5000
        assert c.ui.show_status_bar is True
        assert c.session.save_transcripts is True
        assert c.logging.level == "INFO"
