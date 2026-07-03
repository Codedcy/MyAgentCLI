"""Built-in config tool: config_set — runtime configuration adjustments.

Design doc reference: §九 配置系统 — Layer 2 (runtime overrides)
"""

from __future__ import annotations

from myagent.tools.base import ToolContext, ToolResult


class ConfigSetTool:
    name = "config_set"
    description = (
        "Update a configuration value at runtime during a conversation. "
        "Use this when the user asks to change a setting like thinking mode, "
        "concurrency limit, tool timeout, etc. Changes apply only to the "
        "current session and are not persisted unless the user explicitly "
        "asks to save them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Dot-separated config path, e.g. 'model.thinking', "
                    "'subagents.max_concurrent', 'tools.shell_timeout_seconds', "
                    "'tools.tool_result_max_chars', 'dream.enabled', "
                    "'permissions.default_mode', 'ui.show_status_bar'"
                ),
            },
            "value": {
                "description": (
                    "The new value. Numbers passed as int/float, booleans as bool, "
                    "strings as string."
                ),
            },
        },
        "required": ["key", "value"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        key = params["key"]
        value = params["value"]

        config_loader = getattr(context, "config_loader", None)
        if config_loader is None:
            return ToolResult(
                error="Config loader not available in tool context. "
                      "Runtime config changes are not supported."
            )

        # Validate known config paths
        valid_keys = {
            "model.thinking", "model.fallback_models",
            "context.compression.primary_threshold",
            "context.compression.target_after",
            "context.compression.hard_limit",
            "context.compression.minimum_messages",
            "context.compression.minimum_savings",
            "permissions.default_mode",
            "subagents.max_concurrent", "subagents.speculative_exploration",
            "dream.trigger_hours", "dream.trigger_rounds", "dream.enabled",
            "tools.tool_result_max_chars", "tools.shell_timeout_seconds",
            "ui.show_status_bar", "ui.streaming", "ui.syntax_highlight",
            "session.save_transcripts", "session.transcript_format",
            "logging.level", "logging.format", "logging.llm_prompts",
        }
        if key not in valid_keys:
            return ToolResult(
                error=f"Unknown config key: '{key}'. Valid keys: "
                      f"{', '.join(sorted(valid_keys))}"
            )

        try:
            # Validate type coercion for common config values
            validated = self._validate_value(key, value)

            updated_config = config_loader.apply_runtime_override(key, validated)

            # Also update the in-memory config reference on the context
            if context.config is not None:
                self._update_in_memory_config(context.config, key, validated)

            return ToolResult(
                output=f"Config updated: {key} = {validated} (runtime override, not persisted)",
                metadata={"key": key, "value": validated},
            )
        except Exception as e:
            return ToolResult(error=f"Failed to update config '{key}': {e}")

    @staticmethod
    def _validate_value(key: str, value):
        """Validate and coerce value to the expected type for known config keys."""
        # Boolean keys
        bool_keys = {"dream.enabled", "ui.show_status_bar", "ui.streaming",
                     "ui.syntax_highlight", "logging.llm_prompts",
                     "subagents.speculative_exploration", "session.save_transcripts"}
        # Float keys
        float_keys = {"context.compression.primary_threshold",
                      "context.compression.target_after",
                      "context.compression.hard_limit",
                      "context.compression.minimum_savings"}
        # Int keys
        int_keys = {"subagents.max_concurrent", "dream.trigger_hours",
                    "dream.trigger_rounds", "tools.tool_result_max_chars",
                    "tools.shell_timeout_seconds",
                    "context.compression.minimum_messages"}
        # String keys
        string_keys = {"model.thinking", "permissions.default_mode",
                       "logging.level", "logging.format"}

        if key in bool_keys:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)
        if key in float_keys:
            return float(value)
        if key in int_keys:
            return int(value)
        if key in string_keys:
            return str(value)

        return value

    @staticmethod
    def _update_in_memory_config(config, key: str, value) -> None:
        """Update the in-memory AppConfig object to reflect runtime changes.

        This ensures that other components reading from the config object
        see the updated value immediately, without requiring a full reload.
        """
        keys = key.split(".")
        obj = config
        for part in keys[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return
        if hasattr(obj, keys[-1]):
            setattr(obj, keys[-1], value)
