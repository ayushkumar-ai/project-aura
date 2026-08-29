from core.context import AURAContext
from core.models import AURARequest, AURAResponse
from core.policy import Policy, PolicyDecision
from evaluation.evaluator import Evaluator
from evaluation.models import EvaluationResult
from interfaces.model import ModelInterface
from interfaces.memory import MemoryInterface
from core.history import ConversationHistory
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector


class Orchestrator:
    """Coordinates the AURA request execution pipeline."""

    def __init__(
        self,
        model: ModelInterface,
        policy: Policy,
        memory: MemoryInterface | None = None,
        history: ConversationHistory | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_selector: ToolSelector | None = None,
    ):
        self.model = model
        self.policy = policy
        self.memory = memory
        self.history = history
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.tool_selector = tool_selector

        if self.tool_executor is None and self.tool_selector is not None:
            self.tool_executor = ToolExecutor(self.tool_selector.registry)

        if self.tool_executor is None and self.tool_registry is not None:
            self.tool_executor = ToolExecutor(self.tool_registry)

    def _build_context(self, request: AURARequest) -> AURAContext:
        """Build execution context with optional memory."""

        context = AURAContext(
            request=request,
            request_id=request.request_id,
            history=(
                self.history
                if self.history is not None
                else ConversationHistory()
            ),
        )

        if self.memory is not None:
            memory_key = request.metadata.get("memory_key")

            if memory_key is not None:
                memory_value = self.memory.retrieve(memory_key)

                if memory_value is not None:
                    context.state["memory"] = memory_value

        return context

    def _resolve_tool(
        self,
        request: AURARequest,
    ) -> tuple[str | None, str | None, bool]:
        """Resolve a tool name, optional input, and whether it was explicit."""

        explicit_tool = request.metadata.get("tool")
        tool_input = request.metadata.get("tool_input")

        # Explicit tool request from request metadata.
        if explicit_tool is not None:
            return explicit_tool, tool_input, True

        # No selector means no natural-language tool resolution.
        if self.tool_selector is None:
            return None, None, False

        # Natural-language tool selection.
        tool_name = self.tool_selector.select(request.user_input)


        return tool_name, tool_input, False

    def _execute_tool(
        self,
        tool_name: str,
        tool_input: str | None,
        request: AURARequest,
    ) -> str:
        """Prepare input and execute the resolved tool."""

        if self.tool_executor is None:
            raise RuntimeError("Tool executor is not configured.")

        if tool_input is None:
            tool_input = self.tool_executor.prepare_input(
                tool_name=tool_name,
                request=request.user_input,
            )

        return self.tool_executor.execute(
            tool_name=tool_name,
            tool_input=tool_input,
        )

    def run(self, request: AURARequest) -> AURAResponse:
        """Execute a request through policy, tools, memory, and model layers."""

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

        if self.tool_executor is not None or self.tool_selector is not None:
            try:
                tool_name, tool_input, explicit_tool = self._resolve_tool(
                    request
                )

            except KeyError:
                unknown_tool = request.user_input.strip()

                return AURAResponse(
                    request_id=context.request_id,
                    content=f"Tool '{unknown_tool}' is not available.",
                    metadata={
                        "tool": unknown_tool,
                        "policy": decision.value,
                        "error": "tool_not_found",
                    },
                )

            if tool_name is not None:

                # An explicitly requested tool without explicit input
                # preserves the model-fallback behavior.
                if explicit_tool and tool_input is None:
                    tool_name = None

                else:
                    try:
                        tool_result = self._execute_tool(
                            tool_name=tool_name,
                            tool_input=tool_input,
                            request=request,
                        )

                        if self.history is not None:
                            self.history.add_turn(
                                user_input=request.user_input,
                                assistant_output=tool_result,
                            )

                        return AURAResponse(
                            request_id=context.request_id,
                            content=tool_result,
                            metadata={
                                "tool": tool_name,
                                "policy": decision.value,
                            },
                        )

                    except KeyError:
                        return AURAResponse(
                            request_id=context.request_id,
                            content=f"Tool '{tool_name}' is not available.",
                            metadata={
                                "tool": tool_name,
                                "policy": decision.value,
                                "error": "tool_not_found",
                            },
                        )

                    except Exception:
                        return AURAResponse(
                            request_id=context.request_id,
                            content=(
                                f"Tool '{tool_name}' failed during execution."
                            ),
                            metadata={
                                "tool": tool_name,
                                "policy": decision.value,
                                "error": "tool_execution_failed",
                            },
                        )

        # Default model path.
        prompt_parts = []

        if "memory" in context.state:
            prompt_parts.append(f"Memory: {context.state['memory']}")

        if context.history.turns:
            history_text = "\n".join(
                (
                    f"User: {turn.user_input}\n"
                    f"Assistant: {turn.assistant_output}"
                )
                for turn in context.history.turns
            )
            prompt_parts.append(f"History:\n{history_text}")

        prompt_parts.append(f"User: {request.user_input}")

        prompt = "\n".join(prompt_parts)

        response = self.model.generate(
            prompt,
            request_id=context.request_id,
        )

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