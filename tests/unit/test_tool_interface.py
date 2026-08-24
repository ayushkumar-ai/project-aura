import pytest

from interfaces.tool import ToolInterface


def test_tool_interface_requires_execute():
    with pytest.raises(TypeError):
        ToolInterface()


class FakeTool(ToolInterface):
    """Minimal tool implementation used only for testing."""

    def execute(self, input_data: str) -> str:
        return f"Tool executed: {input_data}"


def test_valid_tool_implementation():
    tool = FakeTool()

    result = tool.execute("Hello AURA")

    assert result == "Tool executed: Hello AURA"


def test_tool_interface_requires_description():
    class TestTool(ToolInterface):
        def execute(self, input_data: str) -> str:
            return input_data

    tool = TestTool()

    assert tool.description == "Test tool"


def test_tool_interface_provides_default_name():
    tool = FakeTool()

    assert tool.name == "fake"


def test_tool_interface_provides_default_empty_keywords():
    tool = FakeTool()

    assert tool.keywords == ()    