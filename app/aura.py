from core.models import AURARequest
from core.orchestrator import Orchestrator


class AURA:
    """Persistent application-level runtime for Project AURA."""

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator

    def run(self, user_input: str):
        """Run a user input through the persistent AURA runtime."""
        request = AURARequest(user_input=user_input)
        return self.orchestrator.run(request)
