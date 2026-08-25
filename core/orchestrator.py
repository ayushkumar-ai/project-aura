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
            history=self.history if self.history is not None else ConversationHistory(),
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
    ) -> tuple[str | None, str | None]:
        """Resolve a tool name and optional explicit tool input."""

        explicit_tool = request.metadata.get("tool")
        tool_input = request.metadata.get("tool_input")

        if explicit_tool is not None:
            return explicit_tool, tool_input

        if self.tool_selector is None:
            return None, None

        try:
            tool_name = self.tool_selector.select(request.user_input)
        except KeyError:
            return None, None

        return tool_name, None


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
        if self.tool_executor is not None or self.tool_selector is not None:
            explicit_tool = request.metadata.get("tool")
            tool_name = explicit_tool
            tool_input = request.metadata.get("tool_input")

            if tool_name is not None:
                try:
                    # Only prepare input for tools selected from natural language.
                    # Explicit tool requests without tool_input preserve the
                    # existing model-fallback behavior.
                    if tool_input is None and explicit_tool is None:
                        tool_input = self.tool_executor.prepare_input(
                            tool_name=tool_name,
                            request=request.user_input,
                        )

                    # If there is still no input, fall back to the model.
                    if tool_input is None:
                        tool_name = None
                    else:
                        tool_result = self.tool_executor.execute(
                            tool_name=tool_name,
                            tool_input=tool_input,
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
                        content=f"Tool '{tool_name}' failed during execution.",
                        metadata={
                            "tool": tool_name,
                            "policy": decision.value,
                            "error": "tool_execution_failed",
                        },
                    )

            if tool_name is None and self.tool_selector is not None:
                try:
                    tool_name = self.tool_selector.select(request.user_input)
                except KeyError:
                    tool_name = request.user_input


            if tool_name is not None:
                try:
                    if tool_input is None:
                        tool_input = self.tool_executor.prepare_input(
                            tool_name=tool_name,
                            request=request.user_input,
                        )

                    tool_result = self.tool_executor.execute(
                        tool_name=tool_name,
                        tool_input=tool_input,
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
                        content=f"Tool '{tool_name}' failed during execution.",
                        metadata={
                            "tool": tool_name,
                            "policy": decision.value,
                            "error": "tool_execution_failed",
                        },
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




