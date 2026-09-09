from typing import Any

from core.models import AURARequest
from core.orchestrator import Orchestrator


class AURA:
    """Persistent application-level runtime for Project AURA."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        agentic_runtime: Any | None = None,
    ):
        self.orchestrator = orchestrator
        self.agentic_runtime = (
            agentic_runtime
            if agentic_runtime is not None
            else getattr(orchestrator, "agentic_runtime", None)
        )

    def run(self, user_input: str):
        """Run a user input through the persistent AURA runtime."""
        request = AURARequest(user_input=user_input)
        return self.run_request(request)

    def run_request(self, request: AURARequest):
        """Run a complete AURA request through the persistent runtime."""
        return self.orchestrator.run(request)

    def run_task(
        self,
        task: str,
        task_id: str | None = None,
        timeout: float | None = None,
    ):
        """Run a multi-step agentic task through the agentic runtime."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.execute_task(
            task=task,
            task_id=task_id,
            timeout=timeout,
        )
