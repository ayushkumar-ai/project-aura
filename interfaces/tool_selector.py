import re

from core.tool_registry import ToolRegistry


class ToolSelector:
    """Selects a registered tool for an AURA request."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def select(self, request: str) -> str | None:
        """Select a tool by exact name or from tool capabilities."""
        if not request.strip():
            raise ValueError("Request cannot be empty")

        normalized_request = request.strip().lower()

        # Exact tool name.
        for name in self.registry.list_tools():
            if normalized_request == name.lower():
                return name

        # Natural-language intent matching using tool capabilities.
        for name in self.registry.list_tools():
            tool = self.registry.get(name)

            pattern = r"\b" + re.escape(name.lower()) + r"\b"
            if re.search(pattern, normalized_request):
                return name

            for keyword in tool.keywords:
                keyword_pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
                if re.search(keyword_pattern, normalized_request):
                    return name

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