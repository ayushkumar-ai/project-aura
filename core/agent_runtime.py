import concurrent.futures
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

from core.model_router import ModelRouter, TaskRequirements
from core.policy import Policy
from core.skill_registry import Skill, SkillRegistry
from interfaces.model import ModelInterface
from interfaces.tool_executor import ToolExecutor

logger = logging.getLogger("aura.agent_runtime")


@dataclass(frozen=True)
class AgentRequest:
    """Represents a request to execute a skill in the agent runtime."""

    skill_name: str
    input_data: Any = field(default_factory=dict)
    request_id: UUID = field(default_factory=uuid4)
    task_requirements: TaskRequirements | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        if not isinstance(self.request_id, UUID):
            raise TypeError("request_id must be an instance of UUID.")

        if self.task_requirements is not None and not isinstance(
            self.task_requirements, TaskRequirements
        ):
            raise TypeError("task_requirements must be an instance of TaskRequirements or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")


@dataclass(frozen=True)
class AgentResult:
    """Represents the outcome of an agent runtime execution."""

    success: bool
    skill_name: str
    output: Any = None
    selected_model_id: str | None = None
    selected_provider_id: str | None = None
    error: str | None = None
    request_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """Coordinates skill resolution, model routing, and safe handler execution."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        model_router: ModelRouter | None = None,
        tool_executor: ToolExecutor | None = None,
        policy: Policy | None = None,
        default_timeout: float | None = None,
    ):
        if not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry.")

        if model_router is not None and not isinstance(model_router, ModelRouter):
            raise TypeError("model_router must be an instance of ModelRouter or None.")

        if tool_executor is not None and not isinstance(tool_executor, ToolExecutor):
            raise TypeError("tool_executor must be an instance of ToolExecutor or None.")

        if policy is not None and not isinstance(policy, Policy):
            raise TypeError("policy must be an instance of Policy or None.")

        self.skill_registry = skill_registry
        self.model_router = model_router
        self.tool_executor = tool_executor
        self.policy = policy
        self.default_timeout = default_timeout

    def _validate_input(self, skill: Skill, input_data: Any) -> str | None:
        """Validate input_data against skill.input_schema. Returns error string if invalid."""
        schema = skill.input_schema
        if not schema:
            return None

        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(input_data, dict):
                return "Input data must be a dictionary for object schema."

            required_fields = schema.get("required", [])
            if isinstance(required_fields, (list, tuple, set)):
                for req_field in required_fields:
                    if req_field not in input_data:
                        return f"Missing required input field: '{req_field}'."

        elif expected_type == "string":
            if not isinstance(input_data, str):
                return "Input data must be a string for string schema."

        elif expected_type == "array":
            if not isinstance(input_data, (list, tuple)):
                return "Input data must be a list/tuple for array schema."

        return None

    def _invoke_handler(
        self,
        handler: Callable[..., Any],
        input_data: Any,
        context: dict[str, Any],
        timeout: float | None = None,
    ) -> Any:
        """Invoke the skill handler supporting both simple and context-aware signatures."""
        sig = inspect.signature(handler)
        params = list(sig.parameters.values())
        param_names = [p.name for p in params]

        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params
        )

        def call_target():
            if has_var_keyword:
                return handler(input_data, **context)
            elif len(params) == 1:
                return handler(input_data)
            elif len(params) == 2 and ("context" in param_names or params[1].default == inspect.Parameter.empty):
                return handler(input_data, context)
            else:
                kwargs = {k: v for k, v in context.items() if k in param_names}
                return handler(input_data, **kwargs)

        if timeout is None:
            return call_target()

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(call_target)
            return future.result(timeout=timeout)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def execute(
        self,
        request: AgentRequest | str,
        timeout: float | None = None,
    ) -> AgentResult:
        """Execute a skill through the agent runtime."""
        if isinstance(request, str):
            request = AgentRequest(skill_name=request)
        elif not isinstance(request, AgentRequest):
            raise TypeError("request must be an instance of AgentRequest or a skill name string.")

        request_id = request.request_id
        skill_name = request.skill_name

        # 1. Resolve skill from SkillRegistry
        if not self.skill_registry.has(skill_name):
            logger.warning("Skill '%s' not found in registry", skill_name)
            return AgentResult(
                success=False,
                skill_name=skill_name,
                error=f"Unknown skill: {skill_name}",
                request_id=request_id,
            )

        skill = self.skill_registry.get(skill_name)

        # 2. Validate input schema
        validation_error = self._validate_input(skill, request.input_data)
        if validation_error is not None:
            logger.warning("Skill input validation failed: %s", validation_error)
            return AgentResult(
                success=False,
                skill_name=skill.name,
                error=f"Invalid skill input: {validation_error}",
                request_id=request_id,
            )

        # 3. Model routing
        selected_model_id: str | None = None
        selected_provider_id: str | None = None
        model_provider: ModelInterface | None = None

        requires_model = bool(skill.required_capabilities) or (request.task_requirements is not None)

        if requires_model:
            if self.model_router is None:
                return AgentResult(
                    success=False,
                    skill_name=skill.name,
                    error="ModelRouter required for skill with capability requirements.",
                    request_id=request_id,
                )

            req_caps = set(skill.required_capabilities)
            pref_model = None
            pref_provider = None

            if request.task_requirements is not None:
                req_caps.update(request.task_requirements.required_capabilities)
                pref_model = request.task_requirements.preferred_model
                pref_provider = request.task_requirements.preferred_provider

            task_req = TaskRequirements(
                required_capabilities=req_caps,
                preferred_model=pref_model,
                preferred_provider=pref_provider,
            )

            try:
                route_result = self.model_router.route(task_req)
                selected_model_id = route_result.model_id
                selected_provider_id = route_result.provider_id
                model_provider = route_result.provider
            except Exception as e:
                logger.warning("Model routing failed for skill '%s': %s", skill.name, e)
                return AgentResult(
                    success=False,
                    skill_name=skill.name,
                    error=f"Model routing failed: {str(e)}",
                    request_id=request_id,
                )

        # 4. Check handler
        if skill.handler is None:
            return AgentResult(
                success=False,
                skill_name=skill.name,
                selected_model_id=selected_model_id,
                selected_provider_id=selected_provider_id,
                error=f"Skill '{skill.name}' has no execution handler.",
                request_id=request_id,
            )

        # 5. Execute handler
        effective_timeout = timeout if timeout is not None else self.default_timeout
        context = {
            "request_id": request_id,
            "skill": skill,
            "model": model_provider,
            "model_id": selected_model_id,
            "provider_id": selected_provider_id,
            "tool_executor": self.tool_executor,
            "policy": self.policy,
        }

        try:
            output = self._invoke_handler(
                skill.handler,
                request.input_data,
                context,
                timeout=effective_timeout,
            )
            return AgentResult(
                success=True,
                skill_name=skill.name,
                output=output,
                selected_model_id=selected_model_id,
                selected_provider_id=selected_provider_id,
                request_id=request_id,
            )
        except concurrent.futures.TimeoutError:
            logger.warning("Execution of skill '%s' timed out", skill.name)
            return AgentResult(
                success=False,
                skill_name=skill.name,
                selected_model_id=selected_model_id,
                selected_provider_id=selected_provider_id,
                error=f"Skill execution timed out after {effective_timeout}s.",
                request_id=request_id,
            )
        except Exception as e:
            logger.warning("Handler execution failed for skill '%s': %s", skill.name, e)
            return AgentResult(
                success=False,
                skill_name=skill.name,
                selected_model_id=selected_model_id,
                selected_provider_id=selected_provider_id,
                error=f"Handler execution failed: {str(e)}",
                request_id=request_id,
            )

    def run(
        self,
        request: AgentRequest | str,
        timeout: float | None = None,
    ) -> AgentResult:
        """Alias for execute."""
        return self.execute(request, timeout=timeout)
