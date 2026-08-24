from core.tool_registry import ToolRegistry


class ToolSelector:
    """Selects a registered tool for an AURA request."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def select(self, request: str) -> str | None:
        """Select a tool by exact name or from a natural-language request."""
        if not request.strip():
            raise ValueError("Request cannot be empty")

        normalized_request = request.strip().lower()

        # Exact tool name: preserve the existing KeyError contract.
        for name in self.registry.list_tools():
            if normalized_request == name.lower():
                return name

        # Natural-language request: select a matching registered tool.
        for name in self.registry.list_tools():
            if name.lower() in normalized_request:
                return name

        # A single unknown identifier is treated as an explicit tool request.
        if " " not in normalized_request:
            raise KeyError(f"Unknown tool: {request}")

        # Natural-language request with no matching tool.
        return None

    def list_tools(self) -> list[str]:
        """Return the names of available tools."""
        return self.registry.list_tools()

    def describe_tools(self) -> dict[str, str]:
        """Return descriptions of available tools."""
        descriptions = {}

        for name in self.registry.list_tools():
            tool = self.registry.get(name)
            descriptions[name] = tool.description

        return descriptions