"""Tests for ToolRegistry."""

import pytest

from myagent.tools.base import ToolResult, ToolContext
from myagent.tools.registry import ToolRegistry


class FakeReadTool:
    name = "read"
    description = "Read a file"
    parameters = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    }

    async def execute(self, params, context):
        return ToolResult(output="file content")


class FakeWriteTool:
    name = "write"
    description = "Write a file"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, params, context):
        return ToolResult(output="ok")


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = FakeReadTool()
        registry.register(tool)

        assert registry.get("read") is tool
        assert registry.get("nonexistent") is None

    def test_list_all(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())
        registry.register(FakeWriteTool())

        tools = registry.list_all()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"read", "write"}

    def test_unregister(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())
        assert len(registry) == 1

        registry.unregister("read")
        assert len(registry) == 0
        assert registry.get("read") is None

        # Unregister nonexistent should not raise
        registry.unregister("nonexistent")

    def test_get_schemas(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())
        registry.register(FakeWriteTool())

        schemas = registry.get_schemas()
        assert len(schemas) == 2

        for schema in schemas:
            assert schema["type"] == "function"
            func = schema["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

        names = {s["function"]["name"] for s in schemas}
        assert names == {"read", "write"}

    def test_get_schemas_for_subset(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())
        registry.register(FakeWriteTool())

        schemas = registry.get_schemas_for(["read"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "read"

    def test_get_schemas_for_unknown(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())

        schemas = registry.get_schemas_for(["nonexistent"])
        assert len(schemas) == 0

    def test_contains(self):
        registry = ToolRegistry()
        registry.register(FakeReadTool())

        assert "read" in registry
        assert "write" not in registry

    def test_register_overwrites(self):
        registry = ToolRegistry()
        tool1 = FakeReadTool()
        tool2 = FakeReadTool()  # same name
        registry.register(tool1)
        registry.register(tool2)

        assert registry.get("read") is tool2
