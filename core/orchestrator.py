from core.context import AURAContext
from core.models import AURARequest, AURAResponse
from core.policy import Policy, PolicyDecision
from interfaces.model import ModelInterface


class Orchestrator:
    """Coordinates the AURA request execution pipeline."""

    def __init__(
        self,
        model: ModelInterface,
        policy: Policy,
    ):
        self.model = model
        self.policy = policy

    def run(self, request: AURARequest) -> AURAResponse:
        """Execute a request through policy and model layers."""

        context = AURAContext(
            request=request,
            request_id=request.request_id,
        )

        decision = self.policy.evaluate(request)

        if decision == PolicyDecision.DENY:
            return AURAResponse(
                request_id=context.request_id,
                content="Request denied by policy.",
                metadata={"policy": decision.value},
            )

        response = self.model.generate(request.user_input)

        return AURAResponse(
            request_id=context.request_id,
            content=response.content,
            metadata={
                **response.metadata,
                "policy": decision.value,
            },
        )