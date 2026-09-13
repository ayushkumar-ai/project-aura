import concurrent.futures
import logging
from typing import Any
from uuid import UUID

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
from interfaces.knowledge import KnowledgeInterface, KnowledgeRecord

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
        synthesize_tool_results: bool = False,
        tool_timeout: float | None = None,
        model_timeout: float | None = None,
        agentic_runtime: Any | None = None,
    ):
        self.model = model
        self.policy = policy
        self.memory = memory
        self.history = history
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.tool_selector = tool_selector
        self.knowledge = knowledge
        self.synthesize_tool_results = synthesize_tool_results
        self.tool_timeout = tool_timeout
        self.model_timeout = model_timeout
        self.agentic_runtime = agentic_runtime

        if self.tool_executor is None and self.tool_selector is not None:
            self.tool_executor = ToolExecutor(
                self.tool_selector.registry,
                policy=self.policy,
                timeout=self.tool_timeout,
            )

        if self.tool_executor is None and self.tool_registry is not None:
            self.tool_executor = ToolExecutor(
                self.tool_registry,
                policy=self.policy,
                timeout=self.tool_timeout,
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

            if (
                memory_key is not None
                and isinstance(memory_key, str)
                and memory_key.strip()
            ):
                try:
                    memory_value = self.memory.retrieve(memory_key)
                except Exception:
                    logger.warning(
                        "Memory retrieval failed for request %s",
                        context.request_id,
                    )
                    raise

                if (
                    memory_value is not None
                    and isinstance(memory_value, str)
                    and memory_value.strip()
                ):
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

        if self.tool_timeout is not None:
            try:
                return self.tool_executor.execute(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    timeout=self.tool_timeout,
                )
            except TypeError:
                return self.tool_executor.execute(
                    tool_name=tool_name,
                    tool_input=tool_input,
                )

        return self.tool_executor.execute(
            tool_name=tool_name,
            tool_input=tool_input,
        )

    def _generate_model_response(
        self,
        prompt: str,
        request_id: UUID,
    ) -> AURAResponse:
        """Generate model response with optional timeout boundary."""
        if self.model_timeout is None:
            return self.model.generate(
                prompt,
                request_id=request_id,
            )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                self.model.generate,
                prompt,
                request_id=request_id,
            )
            return future.result(timeout=self.model_timeout)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _retrieve_knowledge(
        self,
        request: AURARequest,
        context: AURAContext,
    ) -> list[KnowledgeRecord]:
        """Retrieve relevant knowledge records from the configured knowledge store."""

        if self.knowledge is None:
            return []

        try:
            records = self.knowledge.retrieve(request.user_input)
            if not isinstance(records, list):
                return []
            valid_records = []
            for record in records:
                if isinstance(record, KnowledgeRecord):
                    if (
                        isinstance(record.content, str)
                        and record.content.strip()
                    ):
                        valid_records.append(record)
            return valid_records
        except Exception:
            logger.warning(
                "Knowledge retrieval failed for request %s",
                context.request_id,
            )
            raise

    def _build_prompt(
        self,
        request: AURARequest,
        context: AURAContext,
        retrieved_knowledge: list[KnowledgeRecord] | None = None,
        tool_name: str | None = None,
        tool_result: str | None = None,
    ) -> str:
        """Construct the prompt for model generation, including memory, history, knowledge, and optional tool output."""

        knowledge_records = retrieved_knowledge or []

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

        if knowledge_records:
            knowledge_text = "\n".join(
                f"Knowledge: {record.content}\nSource: {record.source}"
                for record in knowledge_records
            )
            prompt_parts.append(knowledge_text)

        if prompt_parts:
            prompt_parts.append(f"User: {request.user_input}")
            if tool_name is not None and tool_result is not None:
                prompt_parts.append(
                    f"Tool '{tool_name}' Output: {tool_result}"
                )
            return "\n".join(prompt_parts)
        else:
            if tool_name is not None and tool_result is not None:
                return (
                    f"User: {request.user_input}\n"
                    f"Tool '{tool_name}' Output: {tool_result}"
                )
            return request.user_input

    def run(self, request: AURARequest) -> AURAResponse:
        """Execute a request through policy, tools, memory, and model layers."""

        logger.info("Received request %s", request.request_id)

        try:
            decision = self.policy.evaluate(request)
        except Exception as e:
            logger.warning("Policy evaluation error for request %s: %s", request.request_id, type(e).__name__)
            return AURAResponse(
                request_id=request.request_id,
                content="Request denied by policy due to evaluation error.",
                metadata={"policy": "deny", "error": "policy_evaluation_error"},
            )

        if getattr(decision, "value", str(decision)).lower() != "allow":
            logger.warning("Request %s denied by policy", request.request_id)
            return AURAResponse(
                request_id=request.request_id,
                content="Request denied by policy.",
                metadata={"policy": getattr(decision, "value", "deny")},
            )

        logger.info("Request %s allowed by policy", request.request_id)

        is_agentic = (
            request.metadata.get("agentic") is True
            or str(request.metadata.get("agentic")).lower() == "true"
            or request.metadata.get("mode") == "agentic"
            or request.metadata.get("workflow") is True
            or str(request.metadata.get("workflow")).lower() == "true"
        )
        if is_agentic:
            if self.agentic_runtime is None:
                logger.warning(
                    "Agentic execution requested for %s but agentic_runtime is not configured",
                    request.request_id,
                )
                return AURAResponse(
                    request_id=request.request_id,
                    content="Agentic runtime is not configured.",
                    metadata={
                        "policy": decision.value,
                        "error": "agentic_runtime_not_configured",
                    },
                )
            return self.agentic_runtime.run_request(request)

        try:
            context = self._build_context(request)
        except Exception:
            return AURAResponse(
                request_id=request.request_id,
                content="Memory operation failed.",
                metadata={
                    "policy": decision.value,
                    "error": "memory_operation_failed",
                },
            )

        if self.memory is not None:
            try:
                self.memory.store(
                    str(request.request_id),
                    request.user_input,
                )
            except Exception:
                logger.warning(
                    "Memory store failed for request %s",
                    context.request_id,
                )
                return AURAResponse(
                    request_id=context.request_id,
                    content="Memory operation failed.",
                    metadata={
                        "policy": decision.value,
                        "error": "memory_operation_failed",
                    },
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

                        logger.info(
                            "Tool '%s' executed successfully for request %s",
                            tool_name,
                            context.request_id,
                        )

                        if self.synthesize_tool_results:
                            try:
                                retrieved_knowledge = (
                                    self._retrieve_knowledge(
                                        request, context
                                    )
                                )
                            except Exception:
                                return AURAResponse(
                                    request_id=context.request_id,
                                    content="Knowledge retrieval failed.",
                                    metadata={
                                        "policy": decision.value,
                                        "error": "knowledge_retrieval_failed",
                                    },
                                )

                            prompt = self._build_prompt(
                                request=request,
                                context=context,
                                retrieved_knowledge=retrieved_knowledge,
                                tool_name=tool_name,
                                tool_result=tool_result,
                            )

                            logger.info(
                                "Generating synthesized model response for tool '%s' on request %s",
                                tool_name,
                                context.request_id,
                            )

                            try:
                                response = self._generate_model_response(
                                    prompt=prompt,
                                    request_id=context.request_id,
                                )
                            except TimeoutError:
                                logger.warning(
                                    "Model synthesis timed out for request %s",
                                    context.request_id,
                                )
                                return AURAResponse(
                                    request_id=context.request_id,
                                    content="Model generation timed out.",
                                    metadata={
                                        "policy": decision.value,
                                        "error": "model_timeout",
                                    },
                                )
                            except Exception:
                                logger.warning(
                                    "Model synthesis failed for request %s",
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
                                "Model synthesis succeeded for tool '%s' on request %s",
                                tool_name,
                                context.request_id,
                            )

                            if self.history is not None:
                                self.history.add_turn(
                                    user_input=request.user_input,
                                    assistant_output=response.content,
                                    tool_name=tool_name,
                                    tool_result=tool_result,
                                )

                            return AURAResponse(
                                request_id=context.request_id,
                                content=response.content,
                                metadata={
                                    **response.metadata,
                                    "tool": tool_name,
                                    "policy": decision.value,
                                    "synthesized": "true",
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

                    except TimeoutError:
                        logger.warning(
                            "Tool '%s' execution timed out for request %s",
                            tool_name,
                            context.request_id,
                        )
                        return AURAResponse(
                            request_id=context.request_id,
                            content=f"Tool '{tool_name}' timed out.",
                            metadata={
                                "tool": tool_name,
                                "policy": decision.value,
                                "error": "tool_timeout",
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

        try:
            retrieved_knowledge = self._retrieve_knowledge(request, context)
        except Exception:
            return AURAResponse(
                request_id=context.request_id,
                content="Knowledge retrieval failed.",
                metadata={
                    "policy": decision.value,
                    "error": "knowledge_retrieval_failed",
                },
            )

        prompt = self._build_prompt(
            request=request,
            context=context,
            retrieved_knowledge=retrieved_knowledge,
        )

        logger.info(
            "Generating model response for request %s",
            context.request_id,
        )

        try:
            response = self._generate_model_response(
                prompt=prompt,
                request_id=context.request_id,
            )
        except TimeoutError:
            logger.warning(
                "Model generation timed out for request %s",
                context.request_id,
            )
            return AURAResponse(
                request_id=context.request_id,
                content="Model generation timed out.",
                metadata={
                    "policy": decision.value,
                    "error": "model_timeout",
                },
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
