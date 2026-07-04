"""ConfigLoader — 7-level YAML loader with deep merge.

Priority (low→high), per spec §九:
1. Hardcoded defaults
2. User AGENT.md (~/.myagent/AGENT.md)
3. User config (~/.myagent/config.yaml)
4. Project AGENT.md (.myagent/AGENT.md)
5. Project config (.myagent/config.yaml; or --config path if specified)
6. Runtime overrides (in-memory dict)
7. CLI args

The --config CLI flag changes the source for level 5 (project config),
not a separate priority layer. This keeps exactly 7 levels as documented.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from myagent.config.schema import (
    AppConfig,
)

logger = logging.getLogger("myagent.config")


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
            elif (
                hasattr(f.type, "__origin__")
                and f.type.__origin__ is list
                and isinstance(raw, list)
            ):
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
        config_path: str | None = None,
    ):
        self.project_dir = Path(project_dir)
        self.user_home = Path(user_home)
        self._config_path: str | None = config_path
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
        # When --config is specified, it REPLACES the default project config
        # file path. This keeps exactly 7 priority levels as documented in
        # spec §九, rather than adding an 8th level.
        if self._config_path:
            resolved_path = Path(
                re.sub(r"~(?=/)", lambda m: os.path.expanduser(m.group(0)),
                       self._config_path)
            )
            project_config = self._load_yaml(resolved_path)
        else:
            project_config = self._load_yaml(
                self.project_dir / ".myagent" / "config.yaml"
            )

        # Merge in priority order (low→high): exactly 7 levels
        # Keep raw overlays separate from defaults until after legacy UI
        # migration so explicit status_pane keys win over legacy UI fields.
        merged = deep_merge({}, user_agent_md)
        merged = deep_merge(merged, user_config)
        merged = deep_merge(merged, project_agent_md)
        merged = deep_merge(merged, project_config)
        merged = deep_merge(merged, self._runtime_overrides)

        # Level 7: CLI args
        if cli_args:
            merged = self._apply_cli_args(merged, cli_args)

        merged = self._normalize_ui_config(merged)
        merged = deep_merge(defaults_dict, merged)
        config = _dict_to_dataclass(merged, AppConfig)
        self._validate(config)
        return config

    @staticmethod
    def _normalize_ui_config(merged: dict) -> dict:
        """Normalize legacy UI keys into the status pane config."""
        if not isinstance(merged, dict):
            return merged

        ui_config = merged.get("ui")
        if not isinstance(ui_config, dict):
            return merged

        normalized = dict(merged)
        normalized_ui = dict(ui_config)
        status_pane = normalized_ui.get("status_pane")

        if status_pane is None:
            status_pane = {}
        elif isinstance(status_pane, dict):
            status_pane = dict(status_pane)
        else:
            logger.warning(
                "ui.status_pane must be a mapping; ignoring %s value",
                type(status_pane).__name__,
                extra={"category": "system"},
            )
            status_pane = {}

        if "enabled" not in status_pane and "show_status_bar" in normalized_ui:
            status_pane["enabled"] = normalized_ui["show_status_bar"]
        if "sections" not in status_pane and "status_bar_items" in normalized_ui:
            status_pane["sections"] = normalized_ui["status_bar_items"]

        if status_pane:
            normalized_ui["status_pane"] = status_pane
        else:
            normalized_ui.pop("status_pane", None)

        normalized["ui"] = normalized_ui
        return normalized

    @staticmethod
    def _is_numeric_config_value(value: object) -> bool:
        """Return True for numeric config values while excluding bools."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _validate(config: AppConfig) -> None:
        """Validate config values are within reasonable ranges. Logs warnings
        for out-of-range values and raises ValueError for clearly invalid ones.
        """

        # Validate thresholds are in [0.0, 1.0]
        for name, value in [
            ("context.compression.primary_threshold", config.context.compression.primary_threshold),
            ("context.compression.target_after", config.context.compression.target_after),
            ("context.compression.hard_limit", config.context.compression.hard_limit),
            ("context.compression.minimum_savings", config.context.compression.minimum_savings),
        ]:
            if not 0.0 <= value <= 1.0:
                logger.warning(
                    "Config %s = %.2f is out of range [0.0, 1.0]; using default",
                    name, value,
                    extra={"category": "system"},
                )

        # Validate hard_limit > primary_threshold
        if config.context.compression.hard_limit <= config.context.compression.primary_threshold:
            logger.warning(
                "compression.hard_limit (%.2f) must be > primary_threshold (%.2f)",
                config.context.compression.hard_limit,
                config.context.compression.primary_threshold,
                extra={"category": "system"},
            )

        # Validate concurrency limits are positive
        if (
            config.subagents.max_concurrent is not None
            and config.subagents.max_concurrent < 1
        ):
            logger.warning(
                "subagents.max_concurrent = %d is too low; minimum is 1",
                config.subagents.max_concurrent,
                extra={"category": "system"},
            )

        # Validate time values are positive
        if config.dream.trigger_hours < 1:
            logger.warning(
                "dream.trigger_hours = %d is too low; minimum is 1",
                config.dream.trigger_hours,
                extra={"category": "system"},
            )
        if config.dream.trigger_rounds < 1:
            logger.warning(
                "dream.trigger_rounds = %d is too low; minimum is 1",
                config.dream.trigger_rounds,
                extra={"category": "system"},
            )

        # Validate tool_result_max_chars
        if config.tools.tool_result_max_chars < 100:
            logger.warning(
                "tools.tool_result_max_chars = %d is too small; minimum is 100",
                config.tools.tool_result_max_chars,
                extra={"category": "system"},
            )

        # Validate shell_timeout_seconds
        if config.tools.shell_timeout_seconds < 1:
            logger.warning(
                "tools.shell_timeout_seconds = %d is too low; minimum is 1",
                config.tools.shell_timeout_seconds,
                extra={"category": "system"},
            )

        status_pane = config.ui.status_pane
        numeric_values = {}
        for field_name in [
            "width",
            "min_width",
            "max_width",
            "rail_width",
            "collapse_below_columns",
        ]:
            value = getattr(status_pane, field_name)
            if ConfigLoader._is_numeric_config_value(value):
                numeric_values[field_name] = value
            else:
                logger.warning(
                    "ui.status_pane.%s must be numeric; got %s",
                    field_name,
                    type(value).__name__,
                    extra={"category": "system"},
                )

        width = numeric_values.get("width")
        min_width = numeric_values.get("min_width")
        max_width = numeric_values.get("max_width")
        if width is not None and min_width is not None and width < min_width:
            logger.warning(
                "ui.status_pane.width = %s is below min_width = %s",
                width,
                min_width,
                extra={"category": "system"},
            )
        if width is not None and max_width is not None and width > max_width:
            logger.warning(
                "ui.status_pane.width = %s is above max_width = %s",
                width,
                max_width,
                extra={"category": "system"},
            )

        rail_width = numeric_values.get("rail_width")
        if rail_width is not None and rail_width < 1:
            logger.warning(
                "ui.status_pane.rail_width = %s is too low; minimum is 1",
                rail_width,
                extra={"category": "system"},
            )
        collapse_below_columns = numeric_values.get("collapse_below_columns")
        if collapse_below_columns is not None and collapse_below_columns < 40:
            logger.warning(
                "ui.status_pane.collapse_below_columns = %s is too low; minimum is 40",
                collapse_below_columns,
                extra={"category": "system"},
            )

        chat_window = config.ui.chat_window
        chat_numeric_values = {}
        for field_name in [
            "scrollback_lines",
            "input_min_lines",
            "input_max_lines",
        ]:
            value = getattr(chat_window, field_name)
            if ConfigLoader._is_numeric_config_value(value):
                chat_numeric_values[field_name] = value
            else:
                logger.warning(
                    "ui.chat_window.%s must be numeric; got %s",
                    field_name,
                    type(value).__name__,
                    extra={"category": "system"},
                )

        scrollback_lines = chat_numeric_values.get("scrollback_lines")
        if scrollback_lines is not None and scrollback_lines < 100:
            logger.warning(
                "ui.chat_window.scrollback_lines = %s is too low; minimum is 100",
                scrollback_lines,
                extra={"category": "system"},
            )

        input_min_lines = chat_numeric_values.get("input_min_lines")
        if input_min_lines is not None and input_min_lines < 1:
            logger.warning(
                "ui.chat_window.input_min_lines = %s is too low; minimum is 1",
                input_min_lines,
                extra={"category": "system"},
            )

        input_max_lines = chat_numeric_values.get("input_max_lines")
        if (
            input_min_lines is not None
            and input_max_lines is not None
            and input_max_lines < input_min_lines
        ):
            logger.warning(
                "ui.chat_window.input_max_lines = %s is below input_min_lines = %s",
                input_max_lines,
                input_min_lines,
                extra={"category": "system"},
            )

        valid_input_positions = {"bottom"}
        if chat_window.input_position not in valid_input_positions:
            logger.warning(
                "ui.chat_window.input_position = '%s' is invalid; must be one of %s",
                chat_window.input_position,
                valid_input_positions,
                extra={"category": "system"},
            )

        valid_follow_output = {"auto", "always", "manual"}
        if chat_window.follow_output not in valid_follow_output:
            logger.warning(
                "ui.chat_window.follow_output = '%s' is invalid; must be one of %s",
                chat_window.follow_output,
                valid_follow_output,
                extra={"category": "system"},
            )

        # Validate log level
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if config.logging.level not in valid_levels:
            logger.warning(
                "logging.level = '%s' is invalid; must be one of %s",
                config.logging.level, valid_levels,
                extra={"category": "system"},
            )

        # Validate log format
        valid_formats = {"jsonl", "text", "both"}
        if config.logging.format not in valid_formats:
            logger.warning(
                "logging.format = '%s' is invalid; must be one of %s",
                config.logging.format, valid_formats,
                extra={"category": "system"},
            )

        # Validate retention_days
        if config.logging.retention_days < 1:
            logger.warning(
                "logging.retention_days = %d is too low; minimum is 1",
                config.logging.retention_days,
                extra={"category": "system"},
            )

        # Validate thinking mode
        valid_thinking = {"Think High", "Think Max", "Non-think"}
        if config.model.thinking not in valid_thinking:
            logger.warning(
                "model.thinking = '%s' is invalid; must be one of %s",
                config.model.thinking, valid_thinking,
                extra={"category": "system"},
            )

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
            logger.exception(
                "Failed to parse AGENT.md YAML frontmatter",
                extra={
                    "category": "error",
                    "component": "system",
                    "context": "parse AGENT.md YAML frontmatter",
                },
            )
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
