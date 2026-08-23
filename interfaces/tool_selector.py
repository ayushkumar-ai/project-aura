from core.tool_registry import ToolRegistry


class ToolSelector:
    """Selects a registered tool for an AURA request."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def select(self, request: str) -> str:
        """Select a tool by its registered name."""
        if not request.strip():
            raise ValueError("Request cannot be empty")

        self.registry.get(request)

        return request


    def list_tools(self) -> list[str]:
        """Return the names of available tools."""
        return self.registry.list_tools()