from core.models import AURARequest
from core.orchestrator import Orchestrator


class AURA:
    """Persistent application-level runtime for Project AURA."""

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator

    def run(self, user_input: str):
        """Run a user input through the persistent AURA runtime."""
        request = AURARequest(user_input=user_input)
        return self.run_request(request)

    def run_request(self, request: AURARequest):
        """Run a complete AURA request through the persistent runtime."""
        return self.orchestrator.run(request)