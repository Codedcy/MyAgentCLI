"""Built-in config tool: config_set — runtime configuration adjustments.

Design doc reference: §九 配置系统 — Layer 2 (runtime overrides)
"""

from __future__ import annotations

import dataclasses
import logging
from typing import get_type_hints

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.config")


def _derive_valid_keys(cls=None, prefix: str = "") -> set[str]:
    """Derive all dot-separated config paths from a dataclass hierarchy (G8).

    Recursively walks nested dataclass fields to produce the full set of
    valid config keys (e.g. "model.thinking", "session.sessions_dir").
    Non-dataclass leaf fields become terminal keys.

    Args:
        cls: The dataclass to introspect (defaults to AppConfig).
        prefix: Current key prefix for nested fields.

    Returns:
        Set of dot-separated key strings.
    """
    if cls is None:
        from myagent.config.schema import AppConfig
        cls = AppConfig

    keys: set[str] = set()
    try:
        hints = get_type_hints(cls)
    except Exception:
        logger.exception(
            "Failed to derive config key type hints",
            extra={
                "category": "error",
                "component": "tool",
                "context": "config_set.derive_valid_keys",
            },
        )
        return keys

    for field in dataclasses.fields(cls):
        field_name = field.name
        full_name = f"{prefix}.{field_name}" if prefix else field_name
        field_type = hints.get(field_name)

        # If the field type is a dataclass, recurse into it
        if dataclasses.is_dataclass(field_type):
            nested_keys = _derive_valid_keys(field_type, full_name)
            keys.update(nested_keys)
        else:
            keys.add(full_name)

    return keys


# Build the valid keys set once at module load time
_VALID_KEYS: set[str] = _derive_valid_keys()


def _derive_type_map(cls=None, prefix: str = "") -> dict[str, type]:
    """Derive type mapping for config keys from the dataclass hierarchy (G8).

    Returns a dict mapping dot-separated keys to their Python types,
    which drives type coercion in _validate_value.
    """
    if cls is None:
        from myagent.config.schema import AppConfig
        cls = AppConfig

    type_map: dict[str, type] = {}
    try:
        hints = get_type_hints(cls)
    except Exception:
        logger.exception(
            "Failed to derive config type map",
            extra={
                "category": "error",
                "component": "tool",
                "context": "config_set.derive_type_map",
            },
        )
        return type_map

    for field in dataclasses.fields(cls):
        field_name = field.name
        full_name = f"{prefix}.{field_name}" if prefix else field_name
        field_type = hints.get(field_name)

        if dataclasses.is_dataclass(field_type):
            type_map.update(_derive_type_map(field_type, full_name))
        else:
            # Resolve the origin type (strip Optional, List wrapper, etc.)
            resolved = _resolve_field_type(field_type)
            type_map[full_name] = resolved

    return type_map


def _resolve_field_type(field_type) -> type:
    """Resolve a type annotation to its concrete Python type.

    Strips Optional, handles list[int]/list[str], etc.
    """
    import typing

    origin = typing.get_origin(field_type)
    if origin is not None:
        # Handle Optional[X] = Union[X, None]
        if origin is typing.Union:
            args = typing.get_args(field_type)
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return _resolve_field_type(non_none[0])
            return str
        # Handle list[str], list[int], etc.
        if origin is list:
            args = typing.get_args(field_type)
            if args:
                return _resolve_field_type(args[0])
            return list
        # Handle Literal["a", "b"]
        if origin is typing.Literal or str(origin) == "typing.Literal":
            return str
        return str

    return field_type


# Build the type map once at module load time
_TYPE_MAP: dict[str, type] = _derive_type_map()


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
                    "'permissions.default_mode', 'permissions.auto_allow.commands', "
                    "'permissions.auto_deny.commands', 'ui.show_status_bar', "
                    "'session.sessions_dir', 'model.provider', 'model.model'"
                ),
            },
            "value": {
                "type": ["string", "number", "boolean", "array", "object"],
                "description": (
                    "The new value. Numbers passed as int/float, booleans as bool, "
                    "strings as string. For list keys like auto_allow.commands, "
                    "pass a list of strings."
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

        # G8: Valid keys derived from AppConfig schema — always in sync
        if key not in _VALID_KEYS:
            return ToolResult(
                error=f"Unknown config key: '{key}'. Valid keys: "
                      f"{', '.join(sorted(_VALID_KEYS))}"
            )

        try:
            # Validate type coercion from the schema-derived type map
            validated = self._validate_value(key, value)

            config_loader.apply_runtime_override(key, validated)

            # Also update the in-memory config reference on the context
            if context.config is not None:
                self._update_in_memory_config(context.config, key, validated)

            # Update the live PermissionController for permission-related keys
            if key.startswith("permissions.") and context.permissions is not None:
                self._apply_permission_change(context.permissions, key, validated)

            return ToolResult(
                output=f"Config updated: {key} = {validated} (runtime override, not persisted)",
                metadata={"key": key, "value": validated},
            )
        except Exception as e:
            logger.exception(
                "Failed to update runtime config key '%s'",
                key,
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "config_set.execute",
                },
            )
            return ToolResult(error=f"Failed to update config '{key}': {e}")

    @staticmethod
    def _validate_value(key: str, value):
        """Validate and coerce value to the expected type from the schema (G8).

        Uses _TYPE_MAP derived from AppConfig dataclass annotations.
        Falls back to heuristic detection for keys not in the type map.
        """
        expected_type = _TYPE_MAP.get(key)

        # G8: Type-based coercion from schema-derived type map
        if expected_type is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)
        if expected_type is float:
            return float(value)
        if expected_type is int:
            return int(value)
        if expected_type is str:
            return str(value)
        if expected_type is list:
            if isinstance(value, list):
                return value
            return [value]

        # gap-13-07: No hardcoded bool_keys fallback — all keys with bool type
        # are already handled by the _TYPE_MAP above. If a key is not in
        # _TYPE_MAP, we return the value as-is to avoid incorrect coercion.
        return value

    @staticmethod
    def _apply_permission_change(permissions, key: str, value) -> None:
        """Update the live PermissionController for permission config changes.

        This ensures that permission changes take effect immediately in the
        running session without waiting for a config reload.
        """
        if key == "permissions.default_mode":
            permissions.set_mode(value)
        elif key == "permissions.auto_allow.commands":
            for cmd in (value if isinstance(value, list) else [value]):
                if cmd not in permissions.auto_allow.commands:
                    permissions.auto_allow.commands.append(cmd)
                    permissions._runtime_changes.append({
                        "rule": cmd, "action": "add_allow", "allowed": cmd,
                        "timestamp": __import__("time").time(),
                    })
        elif key == "permissions.auto_allow.paths":
            for path in (value if isinstance(value, list) else [value]):
                if path not in permissions.auto_allow.paths:
                    permissions.auto_allow.paths.append(path)
                    permissions._runtime_changes.append({
                        "rule": path, "action": "add_allow_path", "path": path,
                        "timestamp": __import__("time").time(),
                    })
        elif key == "permissions.auto_allow.levels":
            for level in (value if isinstance(value, list) else [value]):
                if level not in permissions.auto_allow.levels:
                    permissions.auto_allow.levels.append(level)
        elif key == "permissions.auto_deny.commands":
            for cmd in (value if isinstance(value, list) else [value]):
                if cmd not in permissions.auto_deny.commands:
                    permissions.auto_deny.commands.append(cmd)
                    permissions._runtime_changes.append({
                        "rule": cmd, "action": "add_deny", "denied": cmd,
                        "timestamp": __import__("time").time(),
                    })
        elif key == "permissions.auto_deny.paths":
            for path in (value if isinstance(value, list) else [value]):
                if path not in permissions.auto_deny.paths:
                    permissions.auto_deny.paths.append(path)
                    permissions._runtime_changes.append({
                        "rule": path, "action": "add_deny_path", "path": path,
                        "timestamp": __import__("time").time(),
                    })

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
