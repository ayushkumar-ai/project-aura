from tools.echo import EchoTool


def test_echo_tool():
    tool = EchoTool()

    result = tool.execute("Hello AURA")

    assert result == "Hello AURA"


def test_echo_tool_with_different_input():
    tool = EchoTool()

    result = tool.execute("Test input")

    assert result == "Test input"
