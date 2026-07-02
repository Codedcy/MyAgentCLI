"""Tests for Tool protocol and base types."""

from pathlib import Path

from myagent.tools.base import Tool, ToolContext, ToolResult


class TestToolResult:
    def test_defaults(self):
        r = ToolResult()
        assert r.output == ""
        assert r.error is None
        assert r.metadata == {}

    def test_with_output(self):
        r = ToolResult(output="Hello world")
        assert r.output == "Hello world"

    def test_with_error(self):
        r = ToolResult(error="File not found")
        assert r.error == "File not found"
        assert r.output == ""

    def test_with_metadata(self):
        r = ToolResult(output="ok", metadata={"exit_code": 0, "rows": 42})
        assert r.metadata["exit_code"] == 0
        assert r.metadata["rows"] == 42


class TestToolProtocol:
    def test_structural_subtyping(self):
        """Classes matching Tool protocol should pass isinstance check."""
        class MyTool:
            name = "my_tool"
            description = "Does stuff"
            parameters = {"type": "object", "properties": {}}

            async def execute(self, params, context):
                return ToolResult(output="done")

        tool = MyTool()
        assert isinstance(tool, Tool)

    def test_missing_field_not_a_tool(self):
        """Class missing name should not satisfy the protocol."""
        class BadTool:
            description = "Missing name"
            parameters = {}

            async def execute(self, params, context):
                return ToolResult()

        bad = BadTool()
        assert not isinstance(bad, Tool)
