
from core.context import AURAContext
from core.models import AURARequest, AURAResponse
from core.policy import Policy, PolicyDecision
from evaluation.evaluator import Evaluator
from evaluation.models import EvaluationResult
from interfaces.model import ModelInterface
from interfaces.memory import MemoryInterface
from core.history import ConversationHistory
from core.tool_registry import ToolRegistry
from core.history import ConversationHistory

class Orchestrator:
    """Coordinates the AURA request execution pipeline."""


    def __init__(
    self,
        model: ModelInterface,
        policy: Policy,
        memory: MemoryInterface | None = None,
        history: ConversationHistory | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.model = model
        self.policy = policy
        self.memory = memory
        self.history = history
        self.tool_registry = tool_registry


    def _build_context(self, request: AURARequest) -> AURAContext:
        """Build execution context with optional memory."""

        context = AURAContext(
            request=request,
            request_id=request.request_id,
            history=self.history if self.history is not None else ConversationHistory(),
        )

        if self.memory is not None:
            memory_key = request.metadata.get("memory_key")

            if memory_key is not None:
                memory_value = self.memory.retrieve(memory_key)

                if memory_value is not None:
                    context.state["memory"] = memory_value
        return context


    def run(self, request: AURARequest) -> AURAResponse:
        """Execute a request through policy and model layers."""

        context = self._build_context(request)

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

        prompt = request.user_input

        if "memory" in context.state:
            prompt = f"Memory: {context.state['memory']}\nUser: {request.user_input}"

        if context.history.turns:
            history_text = "\n".join(
                f"User: {turn.user_input}\nAssistant: {turn.assistant_output}"
                for turn in context.history.turns
            )

            prompt = (
                f"History:\n"
                f"{history_text}\n"
                f"User: {request.user_input}"
            )

        response = self.model.generate(prompt)

        if self.history is not None:
            self.history.add_turn(
                user_input=request.user_input,
                assistant_output=response.content,
            )
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
