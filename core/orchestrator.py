import logging

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
from interfaces.knowledge import KnowledgeInterface

logger = logging.getLogger("aura.orchestrator")


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
        knowledge: KnowledgeInterface | None = None,
    ):
        self.model = model
        self.policy = policy
        self.memory = memory
        self.history = history
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.tool_selector = tool_selector
        self.knowledge = knowledge

        if self.tool_executor is None and self.tool_selector is not None:
            self.tool_executor = ToolExecutor(
                self.tool_selector.registry,
                policy=self.policy,
            )

        if self.tool_executor is None and self.tool_registry is not None:
            self.tool_executor = ToolExecutor(
                self.tool_registry,
                policy=self.policy,
            )

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

        logger.info("Received request %s", request.request_id)

        context = self._build_context(request)

        decision = self.policy.evaluate(request)

        if decision == PolicyDecision.DENY:
            logger.warning("Request %s denied by policy", context.request_id)
            return AURAResponse(
                request_id=context.request_id,
                content="Request denied by policy.",
                metadata={"policy": decision.value},
            )

        logger.info("Request %s allowed by policy", context.request_id)

        if self.memory is not None:
            self.memory.store(
                str(request.request_id),
                request.user_input,
            )

        if self.tool_executor is not None or self.tool_selector is not None:
            try:
                tool_name, tool_input, explicit_tool = self._resolve_tool(
                    request
                )

            except KeyError:
                unknown_tool = request.user_input.strip()
                logger.warning(
                    "Tool '%s' not found for request %s",
                    unknown_tool,
                    context.request_id,
                )

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
                    logger.info(
                        "Explicit tool '%s' missing input for request %s, falling back to model",
                        tool_name,
                        context.request_id,
                    )
                    tool_name = None

                else:
                    logger.info(
                        "Executing tool '%s' for request %s",
                        tool_name,
                        context.request_id,
                    )
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

                        logger.info(
                            "Tool '%s' executed successfully for request %s",
                            tool_name,
                            context.request_id,
                        )

                        return AURAResponse(
                            request_id=context.request_id,
                            content=tool_result,
                            metadata={
                                "tool": tool_name,
                                "policy": decision.value,
                            },
                        )

                    except PermissionError:
                        logger.warning(
                            "Tool '%s' is unauthorized for request %s",
                            tool_name,
                            context.request_id,
                        )
                        return AURAResponse(
                            request_id=context.request_id,
                            content=f"Tool '{tool_name}' is not authorized.",
                            metadata={
                                "tool": tool_name,
                                "policy": PolicyDecision.DENY.value,
                                "error": "tool_unauthorized",
                            },
                        )

                    except KeyError:
                        logger.warning(
                            "Tool '%s' not found for request %s",
                            tool_name,
                            context.request_id,
                        )
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
                        logger.warning(
                            "Tool '%s' execution failed for request %s",
                            tool_name,
                            context.request_id,
                        )
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

        retrieved_knowledge = []

        if self.knowledge is not None:
            retrieved_knowledge = self.knowledge.retrieve(request.user_input)

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

        if retrieved_knowledge:
            knowledge_text = "\n".join(

                f"Knowledge: {record.content}\nSource: {record.source}"
                for record in retrieved_knowledge
            )
            prompt_parts.append(knowledge_text)

        if prompt_parts:
            prompt_parts.append(f"User: {request.user_input}")
            prompt = "\n".join(prompt_parts)
        else:
            prompt = request.user_input

        logger.info(
            "Generating model response for request %s",
            context.request_id,
        )

        try:
            response = self.model.generate(
                prompt,
                request_id=context.request_id,
            )
        except Exception:
            logger.warning(
                "Model generation failed for request %s",
                context.request_id,
            )
            return AURAResponse(
                request_id=context.request_id,
                content="Model generation failed.",
                metadata={
                    "policy": decision.value,
                    "error": "model_generation_failed",
                },
            )

        logger.info(
            "Model generation succeeded for request %s",
            context.request_id,
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
