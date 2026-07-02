"""ConfigLoader — 7-level YAML loader with deep merge.

Priority (low→high):
1. Hardcoded defaults
2. User AGENT.md (~/.myagent/AGENT.md)
3. User config (~/.myagent/config.yaml)
4. Project AGENT.md (.myagent/AGENT.md)
5. Project config (.myagent/config.yaml)
6. Runtime overrides (in-memory dict)
7. CLI args

Design doc reference: §九 — Config merge strategy
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

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

# CLI arg → (config_path, transform function or None for identity)
def _transform_mode(value: str) -> str:
    """Map CLI mode values to canonical form."""
    mapping = {
        "think-high": "Think High",
        "think-max": "Think Max",
        "non-think": "Non-think",
    }
    return mapping.get(value, value)


_CLI_MAPPING: dict[str, tuple[str, callable | None]] = {
    "mode": ("model.thinking", _transform_mode),
    "dangerously_skip_permissions": ("permissions._skip_all", None),
    "goal": ("session._goal", None),
}

DEFAULT_USER_HOME = Path.home() / ".myagent"
DEFAULT_PROJECT_DIR = Path.cwd()


def _dataclass_to_dict(obj: object) -> dict:
    """Recursively convert a dataclass instance to a plain dict."""
    from dataclasses import fields, is_dataclass

    if not is_dataclass(obj):
        return obj
    result = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        if is_dataclass(value):
            result[f.name] = _dataclass_to_dict(value)
        else:
            result[f.name] = value
    return result


def _dict_to_dataclass(data: dict, cls: type) -> object:
    """Recursively convert a dict to a dataclass instance."""
    from dataclasses import fields, is_dataclass

    kwargs = {}
    for f in fields(cls):
        if f.name in data:
            raw = data[f.name]
            if is_dataclass(f.type) and isinstance(raw, dict):
                kwargs[f.name] = _dict_to_dataclass(raw, f.type)
            elif hasattr(f.type, "__origin__") and f.type.__origin__ is list and isinstance(raw, list):
                kwargs[f.name] = raw
            else:
                kwargs[f.name] = raw
    return cls(**kwargs)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base.

    - dicts: deep-merge recursively
    - lists: completely replace (not append)
    - scalars: replace
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _set_nested_value(data: dict, path: str, value: Any) -> dict:
    """Set a value at a dot-separated path in a nested dict."""
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
    return data


class ConfigLoader:
    """Load and merge configuration from 7 priority levels."""

    def __init__(
        self,
        project_dir: Path = DEFAULT_PROJECT_DIR,
        user_home: Path = DEFAULT_USER_HOME,
    ):
        self.project_dir = Path(project_dir)
        self.user_home = Path(user_home)
        self._runtime_overrides: dict = {}

    # ── public API ─────────────────────────────────────────────

    def load(self, cli_args: dict | None = None) -> AppConfig:
        """Load config with 7-level merge and return AppConfig.

        Synchronous (file I/O is minimal). For async usage, call from
        async context directly — no blocking I/O on network.
        """
        # Level 1: Hardcoded defaults as dict
        defaults_dict = _dataclass_to_dict(AppConfig())

        # Level 2: User AGENT.md
        user_agent_md = self._load_agent_md(self.user_home / "AGENT.md")

        # Level 3: User config
        user_config = self._load_yaml(self.user_home / "config.yaml")

        # Level 4: Project AGENT.md
        project_agent_md = self._load_agent_md(
            self.project_dir / ".myagent" / "AGENT.md"
        )

        # Level 5: Project config
        project_config = self._load_yaml(
            self.project_dir / ".myagent" / "config.yaml"
        )

        # Merge in priority order (low→high)
        merged = deep_merge(defaults_dict, user_agent_md)
        merged = deep_merge(merged, user_config)
        merged = deep_merge(merged, project_agent_md)
        merged = deep_merge(merged, project_config)
        merged = deep_merge(merged, self._runtime_overrides)

        # Level 7: CLI args
        if cli_args:
            merged = self._apply_cli_args(merged, cli_args)

        return _dict_to_dataclass(merged, AppConfig)

    def apply_runtime_override(self, key: str, value: Any) -> AppConfig:
        """Apply a runtime override and return updated config.

        For mid-conversation adjustments (e.g., natural language
        permission changes, mode switches).
        """
        self._runtime_overrides = _set_nested_value(self._runtime_overrides, key, value)
        return self.load()

    # ── internal helpers ───────────────────────────────────────

    @staticmethod
    def _expand_env_vars(content: str) -> str:
        """Expand ${VAR} patterns and ~ in config content.

        1. ${VAR} → os.environ value (unmatched left as-is)
        2. ~ followed by / → os.path.expanduser (path expansion)
        """
        # Expand ${VAR} patterns
        content = re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            content,
        )
        # Expand ~ when followed by / (path context, not YAML null)
        content = re.sub(
            r"~(?=/)",
            lambda m: os.path.expanduser(m.group(0)),
            content,
        )
        return content

    def _load_yaml(self, path: Path) -> dict:
        """Parse YAML file; return {} if missing or empty.

        Expands ${VAR} environment variables and ~ paths before parsing.
        """
        if not path.exists():
            return {}
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        content = self._expand_env_vars(content)
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}

    def _load_agent_md(self, path: Path) -> dict:
        """Extract YAML frontmatter from AGENT.md for config merge.

        Parses YAML frontmatter between --- delimiters.
        Recognised section keys: model, context, permissions, tools, ui,
        subagents, dream, session, logging.
        Unknown keys are silently ignored.
        """
        if not path.exists():
            return {}

        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}

        lines = content.split("\n")
        # Find closing --- after the opening line
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            return {}

        frontmatter_text = "\n".join(lines[1:end_idx])
        if not frontmatter_text.strip():
            return {}

        frontmatter_text = self._expand_env_vars(frontmatter_text)

        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError:
            return {}

        if not isinstance(frontmatter, dict):
            return {}

        # Only allow recognised config section keys
        allowed_keys = {
            "model", "context", "permissions", "tools", "ui",
            "subagents", "dream", "session", "logging",
        }
        result = {}
        for key in frontmatter:
            if key in allowed_keys:
                result[key] = frontmatter[key]

        return result

    def _apply_cli_args(self, merged: dict, cli_args: dict) -> dict:
        """Apply CLI argument overrides (highest priority)."""
        for cli_key, value in cli_args.items():
            if cli_key in _CLI_MAPPING:
                config_path, transform = _CLI_MAPPING[cli_key]
                transformed = transform(value) if transform else value
                merged = _set_nested_value(merged, config_path, transformed)
        return merged
