from interfaces.tool import ToolInterface


class EchoTool(ToolInterface):
    """Simple tool that returns the supplied input."""

    @property
    def description(self) -> str:
        return "Echo tool"

    def execute(self, input_data: str) -> str:
        return input_data