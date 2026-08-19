
from core.context import AURAContext
from core.models import AURARequest, AURAResponse
from core.policy import Policy, PolicyDecision
from evaluation.evaluator import Evaluator
from evaluation.models import EvaluationResult
from interfaces.model import ModelInterface
from interfaces.memory import MemoryInterface


class Orchestrator:
    """Coordinates the AURA request execution pipeline."""


    def __init__(
        self,
        model: ModelInterface,
        policy: Policy,
        memory: MemoryInterface | None = None,
    ):
        self.model = model
        self.policy = policy
        self.memory = memory

    def run(self, request: AURARequest) -> AURAResponse:
        """Execute a request through policy and model layers."""

        context = AURAContext(
            request=request,
            request_id=request.request_id,
        )

        if self.memory is not None:
            self.memory.store(
                str(request.request_id),
                request.user_input,
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

    def evaluate(self, request: AURARequest) -> EvaluationResult:
        """Run a request and evaluate the resulting response."""

        response = self.run(request)

        return Evaluator().evaluate(request, response)