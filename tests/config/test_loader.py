"""Tests for ConfigLoader with 7-level merge."""

import logging
from pathlib import Path

from myagent.config.loader import ConfigLoader
from myagent.config.schema import AppConfig


def _ua(tmp_home_dir):
    """Return the .myagent directory under the temp home (user_home)."""
    return tmp_home_dir / ".myagent"


def write_yaml(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestConfigLoader:
    def test_load_defaults_only(self, tmp_home_dir, tmp_project_dir):
        """Load with no config files present should return defaults."""
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert isinstance(config, AppConfig)
        assert config.model.provider == "deepseek"
        assert config.model.thinking == "Think High"
        assert config.subagents.max_concurrent is None

    def test_user_config_overrides_defaults(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "model:\n  provider: openai\n  thinking: Non-think\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "openai"
        assert config.model.thinking == "Non-think"
        assert config.model.model == "deepseek-v4-pro"  # unchanged default

    def test_project_config_overrides_user(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "model:\n  provider: openai\n",
        )
        write_yaml(
            tmp_project_dir / ".myagent" / "config.yaml",
            "model:\n  provider: anthropic\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "anthropic"  # project wins

    def test_cli_args_highest_priority(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            tmp_project_dir / ".myagent" / "config.yaml",
            "model:\n  thinking: Think Max\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load(cli_args={"mode": "non-think"})
        assert config.model.thinking == "Non-think"  # CLI wins

    def test_deep_merge_dicts(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "logging:\n  level: DEBUG\n  format: text\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.logging.level == "DEBUG"
        assert config.logging.format == "text"
        assert config.logging.max_size_mb == 100  # unchanged default

    def test_list_replacement_not_merge(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n  status_bar_items: [tokens]\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_bar_items == ["tokens"]  # replaced, not appended

    def test_status_pane_enabled_loaded_directly(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n  status_pane:\n    enabled: false\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_pane.enabled is False

    def test_legacy_show_status_bar_maps_to_status_pane_enabled(
        self, tmp_home_dir, tmp_project_dir
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n  show_status_bar: false\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_pane.enabled is False

    def test_legacy_status_bar_items_maps_to_status_pane_sections(
        self, tmp_home_dir, tmp_project_dir
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n  status_bar_items: [tokens]\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_pane.sections == ["tokens"]

    def test_explicit_status_pane_sections_win_over_legacy_status_bar_items(
        self, tmp_home_dir, tmp_project_dir
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_bar_items: [tokens]\n"
            "  status_pane:\n"
            "    sections: [session, health]\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_pane.sections == ["session", "health"]

    def test_explicit_status_pane_sections_win_over_higher_priority_legacy_items(
        self, tmp_home_dir, tmp_project_dir
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_pane:\n"
            "    sections: [session, health]\n",
        )
        write_yaml(
            tmp_project_dir / ".myagent" / "config.yaml",
            "ui:\n  status_bar_items: [tokens]\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.ui.status_pane.sections == ["session", "health"]
        assert config.ui.status_bar_items == ["tokens"]

    def test_non_dict_status_pane_warns_and_uses_default(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n  status_pane: false\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.status_pane.enabled is True
        assert config.ui.status_pane.sections == [
            "session",
            "tokens",
            "goal",
            "subagents",
            "tools",
            "health",
        ]
        assert any(
            "ui.status_pane must be a mapping" in record.getMessage()
            for record in caplog.records
        )

    def test_non_numeric_status_pane_width_warns_without_breaking_load(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_pane:\n"
            "    width: wide\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.status_pane.width == "wide"
        assert any(
            "ui.status_pane.width must be numeric" in record.getMessage()
            for record in caplog.records
        )

    def test_status_pane_validation_warns_when_width_below_min_width(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_pane:\n"
            "    width: 20\n"
            "    min_width: 30\n"
            "    max_width: 48\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        loader.load()

        assert any(
            "ui.status_pane.width = 20 is below min_width = 30"
            in record.getMessage()
            for record in caplog.records
        )

    def test_status_pane_validation_warns_when_width_above_max_width(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_pane:\n"
            "    width: 50\n"
            "    min_width: 28\n"
            "    max_width: 48\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        loader.load()

        assert any(
            "ui.status_pane.width = 50 is above max_width = 48"
            in record.getMessage()
            for record in caplog.records
        )

    def test_status_pane_validation_warns_for_rail_and_collapse_limits(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  status_pane:\n"
            "    rail_width: 0\n"
            "    collapse_below_columns: 39\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        loader.load()

        assert any(
            "ui.status_pane.rail_width = 0 is too low; minimum is 1"
            in record.getMessage()
            for record in caplog.records
        )
        assert any(
            "ui.status_pane.collapse_below_columns = 39 is too low; minimum is 40"
            in record.getMessage()
            for record in caplog.records
        )

    def test_chat_window_config_loaded_directly(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    enabled: false\n"
            "    scrollback_lines: 5000\n"
            "    input_max_lines: 8\n"
            "    follow_output: manual\n"
            "    mouse_support: false\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.enabled is False
        assert config.ui.chat_window.scrollback_lines == 5000
        assert config.ui.chat_window.input_max_lines == 8
        assert config.ui.chat_window.follow_output == "manual"
        assert config.ui.chat_window.mouse_support is False
        assert config.ui.chat_window.input_position == "bottom"
        assert config.ui.chat_window.input_min_lines == 1

    def test_chat_window_validation_warns_when_scrollback_lines_too_low(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    scrollback_lines: 99\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.scrollback_lines == 99
        assert any(
            "ui.chat_window.scrollback_lines = 99 is too low; minimum is 100"
            in record.getMessage()
            and record.category == "system"
            for record in caplog.records
        )

    def test_chat_window_validation_warns_when_input_min_lines_too_low(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    input_min_lines: 0\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.input_min_lines == 0
        assert any(
            "ui.chat_window.input_min_lines = 0 is too low; minimum is 1"
            in record.getMessage()
            and record.category == "system"
            for record in caplog.records
        )

    def test_chat_window_validation_warns_when_input_max_below_input_min(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    input_min_lines: 5\n"
            "    input_max_lines: 4\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.input_min_lines == 5
        assert config.ui.chat_window.input_max_lines == 4
        assert any(
            "ui.chat_window.input_max_lines = 4 is below input_min_lines = 5"
            in record.getMessage()
            and record.category == "system"
            for record in caplog.records
        )

    def test_chat_window_validation_warns_for_unsupported_input_position(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    input_position: top\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.input_position == "top"
        assert any(
            "ui.chat_window.input_position = 'top' is invalid; must be one of"
            in record.getMessage()
            and record.category == "system"
            for record in caplog.records
        )

    def test_chat_window_validation_warns_for_unsupported_follow_output(
        self, tmp_home_dir, tmp_project_dir, caplog
    ):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "ui:\n"
            "  chat_window:\n"
            "    follow_output: sometimes\n",
        )
        caplog.set_level(logging.WARNING, logger="myagent.config")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )

        config = loader.load()

        assert config.ui.chat_window.follow_output == "sometimes"
        assert any(
            "ui.chat_window.follow_output = 'sometimes' is invalid; must be one of"
            in record.getMessage()
            and record.category == "system"
            for record in caplog.records
        )

    def test_missing_config_files_graceful(self, tmp_home_dir, tmp_project_dir):
        """No config files at any level should still load defaults."""
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert isinstance(config, AppConfig)

    def test_runtime_override(self, tmp_home_dir, tmp_project_dir):
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.thinking == "Think High"
        config = loader.apply_runtime_override("model.thinking", "Think Max")
        assert config.model.thinking == "Think Max"

    def test_empty_config_yaml(self, tmp_home_dir, tmp_project_dir):
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert isinstance(config, AppConfig)

    # ── env var expansion ─────────────────────────────────────────

    def test_expand_env_vars_in_config(self, tmp_home_dir, tmp_project_dir, monkeypatch):
        """${VAR} patterns in YAML are expanded from environment."""
        monkeypatch.setenv("MY_MODEL", "gpt-5-mini")
        monkeypatch.setenv("MY_PROVIDER", "openai")
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "model:\n  model: ${MY_MODEL}\n  provider: ${MY_PROVIDER}\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.model == "gpt-5-mini"
        assert config.model.provider == "openai"

    def test_expand_env_vars_unmatched_left_as_is(self, tmp_home_dir, tmp_project_dir, monkeypatch):
        """Unmatched ${VAR} patterns are left unchanged."""
        # Ensure the var is NOT set
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "model:\n  model: ${NONEXISTENT_VAR}\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.model == "${NONEXISTENT_VAR}"

    def test_expand_tilde_in_config(self, tmp_home_dir, tmp_project_dir):
        """~ followed by / is expanded to the home directory."""
        write_yaml(
            _ua(tmp_home_dir) / "config.yaml",
            "logging:\n  dir: ~/my-logs/\nsession:\n  sessions_dir: ~/my-sessions/\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        home = str(Path.home())
        assert config.logging.dir.startswith(home)
        assert config.session.sessions_dir.startswith(home)

    # ── AGENT.md frontmatter ──────────────────────────────────────

    def test_agent_md_yaml_frontmatter(self, tmp_home_dir, tmp_project_dir):
        """AGENT.md YAML frontmatter is parsed and merged into config.

        Project AGENT.md (level 4) overrides user config.yaml (level 3).
        """
        project_agent_md = tmp_project_dir / ".myagent" / "AGENT.md"
        write_yaml(
            project_agent_md,
            "---\n"
            "model:\n  provider: openai\n  thinking: Non-think\n"
            "---\n\n# Project Agent Instructions\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "openai"
        assert config.model.thinking == "Non-think"

    def test_agent_md_no_frontmatter_graceful(self, tmp_home_dir, tmp_project_dir):
        """AGENT.md without frontmatter returns defaults."""
        agent_md = _ua(tmp_home_dir) / "AGENT.md"
        write_yaml(agent_md, "# Just some guidance\n\nNo frontmatter here.\n")
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "deepseek"  # default

    def test_agent_md_unknown_keys_ignored(self, tmp_home_dir, tmp_project_dir):
        """Unknown keys in AGENT.md frontmatter are silently ignored."""
        project_agent_md = tmp_project_dir / ".myagent" / "AGENT.md"
        write_yaml(
            project_agent_md,
            "---\n"
            "model:\n  provider: anthropic\nunknown_key: 42\nanother_bad: foo\n"
            "---\n\n# Content\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "anthropic"
        # Unknown keys should not cause errors

    def test_agent_md_multiple_sections(self, tmp_home_dir, tmp_project_dir):
        """AGENT.md frontmatter can set multiple config sections."""
        project_agent_md = tmp_project_dir / ".myagent" / "AGENT.md"
        write_yaml(
            project_agent_md,
            "---\n"
            "model:\n  provider: openai\n  model: gpt-5-mini\n"
            "tools:\n  shell_timeout_seconds: 300\n"
            "ui:\n  streaming: false\n"
            "---\n\n# Agent Instructions\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.provider == "openai"
        assert config.model.model == "gpt-5-mini"
        assert config.tools.shell_timeout_seconds == 300
        assert config.ui.streaming is False

    def test_agent_md_expand_env_in_frontmatter(self, tmp_home_dir, tmp_project_dir, monkeypatch):
        """AGENT.md frontmatter also expands ${VAR} patterns."""
        monkeypatch.setenv("MY_MODEL", "custom-model-v2")
        project_agent_md = tmp_project_dir / ".myagent" / "AGENT.md"
        write_yaml(
            project_agent_md,
            "---\nmodel:\n  provider: openai\n  model: ${MY_MODEL}\n---\n\n# Content\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        assert config.model.model == "custom-model-v2"

    def test_project_agent_md_level4(self, tmp_home_dir, tmp_project_dir):
        """Project AGENT.md (level 4) overrides user level configs."""
        user_agent_md = _ua(tmp_home_dir) / "AGENT.md"
        write_yaml(
            user_agent_md,
            "---\nmodel:\n  provider: openai\n---\n\n# User AGENT.md\n",
        )
        project_agent_md = tmp_project_dir / ".myagent" / "AGENT.md"
        write_yaml(
            project_agent_md,
            "---\nmodel:\n  provider: anthropic\n---\n\n# Project AGENT.md\n",
        )
        loader = ConfigLoader(
            project_dir=tmp_project_dir,
            user_home=_ua(tmp_home_dir),
        )
        config = loader.load()
        # Project AGENT.md (level 4) overrides user AGENT.md (level 2)
        assert config.model.provider == "anthropic"
