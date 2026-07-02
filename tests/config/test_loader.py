"""Tests for ConfigLoader with 7-level merge."""

from pathlib import Path

import pytest

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
