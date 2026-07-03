"""Static guards for repository exception logging conventions."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from myagent.config.loader import ConfigLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "myagent"
ERROR_COMPONENTS = {"llm", "tool", "agent", "mcp", "system", "memory", "subagent"}
ERROR_COMPONENT_DOCS = [
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-02-myagentcli-design.md",
]
ERROR_COMPONENT_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(ERROR_COMPONENTS)) + r")\b"
)


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _documented_error_component_enums(path: Path) -> list[tuple[int, set[str], str]]:
    enums: list[tuple[int, set[str], str]] = []

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if "component" not in line:
            continue

        components = set(ERROR_COMPONENT_PATTERN.findall(line))
        if len(components) >= 4:
            enums.append((line_number, components, line.strip()))

    return enums


def _location(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(SOURCE_ROOT.parent)}:{getattr(node, 'lineno', '?')}"


def _exception_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return {"<bare>"}
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for element in node.elts:
            names.update(_exception_names(element))
        return names
    return set()


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_has_error_metadata(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict):
        return False

    values: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values, strict=False):
        if key is None:
            continue
        key_name = _literal_string(key)
        if key_name is not None:
            values[key_name] = value

    category = _literal_string(values.get("category", ast.Constant(None)))
    component = _literal_string(values.get("component", ast.Constant(None)))
    context = values.get("context")
    return (
        category == "error"
        and component in ERROR_COMPONENTS
        and context is not None
    )


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_logger_style_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute)


def _call_produces_traceback(call: ast.Call) -> bool:
    name = _call_name(call)
    if name == "exception":
        return True
    exc_info = _keyword(call, "exc_info")
    return isinstance(exc_info, ast.Constant) and exc_info.value is True


def _call_has_error_metadata(call: ast.Call) -> bool:
    extra = _keyword(call, "extra")
    return extra is not None and _dict_has_error_metadata(extra)


def _structured_exception_log_calls(node: ast.AST) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and _is_logger_style_call(call)
        and _call_produces_traceback(call)
        and _call_has_error_metadata(call)
    ]


def test_every_exception_handler_emits_structured_error_log() -> None:
    missing: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handler in [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]:
            if not _structured_exception_log_calls(handler):
                missing.append(_location(path, handler))

    assert missing == []


def test_traceback_logging_uses_error_metadata() -> None:
    missing: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
            if (
                _is_logger_style_call(call)
                and _call_produces_traceback(call)
                and not _call_has_error_metadata(call)
            ):
                missing.append(_location(path, call))

    assert missing == []


def test_documented_error_components_match_static_guard() -> None:
    mismatched: list[str] = []

    for path in ERROR_COMPONENT_DOCS:
        enums = _documented_error_component_enums(path)
        if not enums:
            mismatched.append(f"{path.relative_to(REPO_ROOT)}: missing documented component enum")
            continue

        for line_number, components, line in enums:
            if components != ERROR_COMPONENTS:
                mismatched.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number}: "
                    f"{sorted(components)} != {sorted(ERROR_COMPONENTS)} ({line})"
                )

    assert mismatched == []


def test_malformed_agent_frontmatter_logs_structured_error(
    tmp_path: Path,
    caplog,
) -> None:
    user_home = tmp_path / ".myagent"
    user_home.mkdir()
    (user_home / "AGENT.md").write_text(
        "---\nmodel:\n  provider: [unterminated\n---\n",
        encoding="utf-8",
    )

    loader = ConfigLoader(project_dir=tmp_path / "project", user_home=user_home)

    with caplog.at_level(logging.ERROR, logger="myagent.config"):
        config = loader.load()

    assert config.model.provider == "deepseek"

    records = [
        record
        for record in caplog.records
        if getattr(record, "category", None) == "error"
        and getattr(record, "component", None) == "system"
        and getattr(record, "context", None) == "parse AGENT.md YAML frontmatter"
    ]
    assert records
    assert records[0].exc_info is not None
