import concurrent.futures

from core.policy import Policy, PolicyDecision
from core.tool_registry import ToolRegistry


class ToolExecutor:
    """Executes tools registered with AURA."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: Policy | None = None,
    ):
        self.registry = registry
        self.policy = policy
    def execute(
        self,
        tool_name: str,
        tool_input: str,
        timeout: float | None = None,
    ) -> str:
        """Execute a registered tool with the supplied input."""
        if not tool_name.strip():
            raise ValueError("Tool name cannot be empty.")

        if not tool_input.strip():
            raise ValueError("Tool input cannot be empty.")

        tool = self.registry.get(tool_name)

        if self.policy is not None:
            decision = self.policy.authorize_tool(tool_name)
            if decision != PolicyDecision.ALLOW:
                raise PermissionError(
                    f"Tool '{tool_name}' is not authorized."
                )

        if timeout is None:
            return tool.execute(tool_input)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(tool.execute, tool_input)
            return future.result(timeout=timeout)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    def list_tools(self) -> list[str]:
        """Return the names of available tools."""
        return self.registry.list_tools()

    def prepare_input(self, tool_name: str, request: str) -> str:
        """Prepare user input using the selected tool."""
        tool = self.registry.get(tool_name)
        return tool.prepare_input(request)
