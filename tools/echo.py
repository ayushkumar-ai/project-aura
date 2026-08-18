from interfaces.tool import ToolInterface


class EchoTool(ToolInterface):
    """Simple tool that returns the supplied input."""

    def execute(self, input_data: str) -> str:
        return input_data