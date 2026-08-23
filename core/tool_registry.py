from interfaces.tool import ToolInterface


class ToolRegistry:
    """Registry for tools available to AURA."""

    def __init__(self):
        self._tools: dict[str, ToolInterface] = {}

    def register(self, name: str, tool: ToolInterface) -> None:
        """Register a tool under a unique name."""
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")

        self._tools[name] = tool

    def get(self, name: str) -> ToolInterface:
        """Retrieve a registered tool by name."""
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")

        return self._tools[name]


    def list_tools(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools.keys())
