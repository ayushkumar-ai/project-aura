from core.tool_registry import ToolRegistry


class ToolExecutor:
    """Executes tools registered with AURA."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute(self, tool_name: str, tool_input: str) -> str:
        """Execute a registered tool with the supplied input."""
        if not tool_name.strip():
            raise ValueError("Tool name cannot be empty.")

        if not tool_input.strip():
            raise ValueError("Tool input cannot be empty.")

        tool = self.registry.get(tool_name)
        return tool.execute(tool_input)

    def list_tools(self) -> list[str]:
        """Return the names of available tools."""
        return self.registry.list_tools()

    def prepare_input(self, tool_name: str, request: str) -> str:
        """Prepare user input using the selected tool."""
        tool = self.registry.get(tool_name)
        return tool.prepare_input(request)