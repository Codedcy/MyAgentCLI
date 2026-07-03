"""Static guards for repository exception logging conventions."""

from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "myagent"
ERROR_COMPONENTS = {"llm", "tool", "agent", "mcp", "system", "memory", "subagent"}


def _python_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


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


def _is_broad_exception(handler: ast.ExceptHandler) -> bool:
    return bool(_exception_names(handler.type) & {"<bare>", "Exception", "BaseException"})


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


def _call_has_exc_info(call: ast.Call) -> bool:
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
        and _call_has_exc_info(call)
        and _call_has_error_metadata(call)
    ]


def test_broad_exception_handlers_emit_structured_error_log() -> None:
    missing: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handler in [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]:
            if _is_broad_exception(handler) and not _structured_exception_log_calls(handler):
                missing.append(_location(path, handler))

    assert missing == []


def test_traceback_logging_uses_error_metadata() -> None:
    missing: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
            if _call_has_exc_info(call) and not _call_has_error_metadata(call):
                missing.append(_location(path, call))

    assert missing == []
