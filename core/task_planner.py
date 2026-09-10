import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.capability_registry import ModelCapability
from core.model_router import ModelRouter, TaskRequirements
from core.skill_registry import SkillRegistry
from core.provenance import TaintedValue, is_tainted, render_for_prompt
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    StepStatus,
    serialize_agent_plan,
    deserialize_agent_plan,
)
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.task_planner")


@dataclass(frozen=True)
class PlanStep:
    """Represents a single executable step in an ExecutionPlan."""

    step_id: str
    skill_name: str
    input_data: Any = field(default_factory=dict)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    task_requirements: TaskRequirements | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        # Normalize dependencies
        deps: list[str] = []
        if isinstance(self.dependencies, (list, tuple, set, frozenset)):
            for d in self.dependencies:
                if not isinstance(d, str) or not d.strip():
                    raise ValueError("Dependency ID must be a non-empty string.")
                deps.append(d.strip())
        else:
            raise TypeError("dependencies must be a sequence of strings.")
        object.__setattr__(self, "dependencies", tuple(deps))

        if self.task_requirements is not None and not isinstance(
            self.task_requirements, TaskRequirements
        ):
            raise TypeError("task_requirements must be an instance of TaskRequirements or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")


@dataclass(frozen=True)
class ExecutionPlan:
    """Represents an ordered, structured multi-step execution plan."""

    steps: tuple[PlanStep, ...]
    plan_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")

        if isinstance(self.steps, (list, tuple)):
            for s in self.steps:
                if not isinstance(s, PlanStep):
                    raise TypeError("All items in steps must be PlanStep instances.")
            object.__setattr__(self, "steps", tuple(self.steps))
        else:
            raise TypeError("steps must be a sequence of PlanStep instances.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")


@dataclass(frozen=True)
class ReplanContext:
    """Structured context provided to TaskPlanner for generating an adapted replacement plan."""

    task: str
    failed_step_id: str
    error_message: str
    completed_steps: tuple[str, ...] = field(default_factory=tuple)
    step_outputs: dict[str, Any] = field(default_factory=dict)
    original_plan_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("task must be a non-empty string.")
        if not isinstance(self.failed_step_id, str) or not self.failed_step_id.strip():
            raise ValueError("failed_step_id must be a non-empty string.")
        if not isinstance(self.error_message, str):
            raise TypeError("error_message must be a string.")
        if not isinstance(self.completed_steps, (list, tuple, set, frozenset)):
            raise TypeError("completed_steps must be a sequence of strings.")
        object.__setattr__(self, "completed_steps", tuple(str(s).strip() for s in self.completed_steps if str(s).strip()))
        if not isinstance(self.step_outputs, dict):
            raise TypeError("step_outputs must be a dict.")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")


class TaskPlanner:
    """Creates, generates, validates, and adapts deterministic execution plans."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        model: ModelInterface | None = None,
        model_router: ModelRouter | None = None,
        memory_manager: Any | None = None,
        heuristic_calibrator: Any | None = None,
    ):
        if not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry.")
        if model is not None and not isinstance(model, ModelInterface):
            raise TypeError("model must be an instance of ModelInterface or None.")
        if model_router is not None and not isinstance(model_router, ModelRouter):
            raise TypeError("model_router must be an instance of ModelRouter or None.")

        self.skill_registry = skill_registry
        self.model = model
        self.model_router = model_router
        self.memory_manager = memory_manager
        self.heuristic_calibrator = heuristic_calibrator

    def create_plan(
        self,
        steps: Sequence[PlanStep],
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Create and validate an ExecutionPlan from an explicit sequence of steps."""
        p_id = str(plan_id) if plan_id is not None else str(uuid4())
        meta = metadata if metadata is not None else {}
        plan = ExecutionPlan(steps=tuple(steps), plan_id=p_id, metadata=meta)
        self.validate_plan(plan)
        return plan

    def _build_planning_prompt(self, task: str) -> str:
        """Construct a structured prompt describing available skills to the model."""
        available_skills = self.skill_registry.list_skills()
        skills_desc = []
        for s in available_skills:
            caps = ", ".join(sorted(s.required_capabilities)) if s.required_capabilities else "none"
            tools = ", ".join(sorted(s.tools)) if s.tools else "none"
            skills_desc.append(
                f"- Name: {s.name}\n"
                f"  Description: {s.description}\n"
                f"  Required Capabilities: {caps}\n"
                f"  Tools: {tools}"
            )
        skills_text = "\n".join(skills_desc) if skills_desc else "No skills registered."

        mem_section = ""
        if self.memory_manager is not None and hasattr(self.memory_manager, "build_memory_context_prompt"):
            mem_text = self.memory_manager.build_memory_context_prompt(query=task)
            if mem_text:
                mem_section = f"\nRelevant Memory & Context:\n{mem_text}\n"

        heuristics_section = ""
        if self.heuristic_calibrator is not None and hasattr(self.heuristic_calibrator, "list_promoted_rules"):
            promoted = self.heuristic_calibrator.list_promoted_rules()
            usable = [
                r for r in promoted
                if not hasattr(self.heuristic_calibrator, "is_rule_usable") or self.heuristic_calibrator.is_rule_usable(r.rule_id)
            ]
            if usable:
                rules_text = "\n".join(f"- Rule {r.rule_id}: {r.trigger_condition} (Confidence: {r.calibrated_confidence:.2f})" for r in usable[:5])
                heuristics_section = f"\nCalibrated Heuristic Rules (PROMOTED):\n{rules_text}\n"

        return (
            "You are a task planner in AURA.\n"
            "Decompose the following task into an execution plan using ONLY the available skills.\n\n"
            f"Available Skills:\n{skills_text}\n\n"
            f"{mem_section}"
            f"{heuristics_section}"
            f"Task to accomplish:\n{task.strip()}\n\n"
            "Output the execution plan as a JSON object with a 'steps' list where each step has:\n"
            "- 'step_id': unique string (e.g. 'step_1')\n"
            "- 'skill_name': name of an available skill\n"
            "- 'input_data': input payload for the skill (optional dict or value)\n"
            "- 'dependencies': list of prerequisite step_ids (can be empty)\n\n"
            "Respond ONLY with valid JSON."
        )

    def _build_replanning_prompt(self, context: ReplanContext) -> str:
        """Construct a structured prompt for generating a replacement plan given failure context."""
        available_skills = self.skill_registry.list_skills()
        skills_desc = []
        for s in available_skills:
            caps = ", ".join(sorted(s.required_capabilities)) if s.required_capabilities else "none"
            tools = ", ".join(sorted(s.tools)) if s.tools else "none"
            skills_desc.append(
                f"- Name: {s.name}\n"
                f"  Description: {s.description}\n"
                f"  Required Capabilities: {caps}\n"
                f"  Tools: {tools}"
            )
        skills_text = "\n".join(skills_desc) if skills_desc else "No skills registered."

        completed_desc = ", ".join(context.completed_steps) if context.completed_steps else "None"

        outputs_summary = []
        for k, v in sorted(context.step_outputs.items()):
            if isinstance(v, TaintedValue) or is_tainted(v):
                rendered_v = render_for_prompt(v, wrap_untrusted=True)
                outputs_summary.append(f"  * {k}: {rendered_v[:400]}")
            else:
                outputs_summary.append(f"  * {k}: {str(v)[:200]}")
        outputs_text = "\n".join(outputs_summary) if outputs_summary else "  None"

        mem_section = ""
        if self.memory_manager is not None and hasattr(self.memory_manager, "build_memory_context_prompt"):
            mem_text = self.memory_manager.build_memory_context_prompt(query=context.task)
            if mem_text:
                mem_section = f"\nRelevant Memory & Context:\n{mem_text}\n"

        heuristics_section = ""
        if self.heuristic_calibrator is not None and hasattr(self.heuristic_calibrator, "list_promoted_rules"):
            promoted = self.heuristic_calibrator.list_promoted_rules()
            usable = [
                r for r in promoted
                if not hasattr(self.heuristic_calibrator, "is_rule_usable") or self.heuristic_calibrator.is_rule_usable(r.rule_id)
            ]
            if usable:
                rules_text = "\n".join(f"- Rule {r.rule_id}: {r.trigger_condition} (Confidence: {r.calibrated_confidence:.2f})" for r in usable[:5])
                heuristics_section = f"\nCalibrated Heuristic Rules (PROMOTED):\n{rules_text}\n"

        return (
            "You are an adaptive task planner in AURA.\n"
            "A multi-step workflow failed during execution. Create a replacement execution plan to achieve the task.\n\n"
            f"Available Skills:\n{skills_text}\n\n"
            f"{mem_section}"
            f"{heuristics_section}"
            f"Overall Task Goal:\n{context.task.strip()}\n\n"
            f"Execution Failure Context:\n"
            f"- Completed Steps (will not be re-executed): {completed_desc}\n"
            f"- Completed Step Outputs:\n{outputs_text}\n"
            f"- Failed Step: {context.failed_step_id}\n"
            f"- Failure Reason: {context.error_message}\n\n"
            "Output the replacement execution plan as a JSON object with a 'steps' list where each step has:\n"
            "- 'step_id': unique string\n"
            "- 'skill_name': name of an available skill\n"
            "- 'input_data': input payload for the skill (optional)\n"
            "- 'dependencies': list of prerequisite step_ids (can include completed step_ids)\n\n"
            "Respond ONLY with valid JSON."
        )

    def _extract_json_content(self, raw_content: str) -> str:
        """Extract JSON substring, cleanly stripping markdown code fences if present."""
        content = raw_content.strip()

        if "```" in content:
            start_idx = content.find("```")
            end_idx = content.rfind("```")
            if start_idx != -1 and end_idx != -1 and start_idx != end_idx:
                block = content[start_idx : end_idx + 3]
                lines = block.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                fenced = "\n".join(lines).strip()
                if fenced:
                    content = fenced

        return content

    def _parse_model_plan(self, raw_content: str | None) -> list[PlanStep]:
        """Parse untrusted model output text into validated PlanStep objects."""
        if raw_content is None or not isinstance(raw_content, str) or not raw_content.strip():
            raise ValueError("Model returned an empty or whitespace-only response.")

        content = self._extract_json_content(raw_content)

        try:
            data = json.loads(content)
        except Exception as e:
            raise ValueError(f"Failed to parse model plan output as JSON: {e}")

        if not isinstance(data, (dict, list)):
            raise ValueError("Model output JSON must be an object with 'steps' or a list of steps.")

        if isinstance(data, dict):
            if "steps" not in data:
                raise ValueError("Model output JSON missing 'steps' key.")
            raw_steps = data.get("steps")
            if raw_steps is None:
                raise ValueError("Model output 'steps' key cannot be null.")
        else:
            raw_steps = data

        if not isinstance(raw_steps, list):
            raise ValueError("'steps' must be a list in model output.")

        if not raw_steps:
            raise ValueError("Model generated an empty list of steps.")

        plan_steps = []
        for idx, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                raise ValueError(f"Step at index {idx} in model output must be a JSON object/dictionary.")

            step_id = raw_step.get("step_id")
            skill_name = raw_step.get("skill_name")

            if not step_id or not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Step at index {idx} must have a valid non-empty string 'step_id'.")
            step_id = step_id.strip()

            if not skill_name or not isinstance(skill_name, str) or not skill_name.strip():
                raise ValueError(f"Step '{step_id}' must have a valid non-empty string 'skill_name'.")
            skill_name = skill_name.strip()

            # Dependencies validation
            raw_deps = raw_step.get("dependencies", [])
            if raw_deps is None:
                raw_deps = []
            if not isinstance(raw_deps, (list, tuple)):
                raise ValueError(f"Dependencies for step '{step_id}' must be a list or tuple of strings.")

            deps = []
            for d in raw_deps:
                if not isinstance(d, str) or not d.strip():
                    raise ValueError(f"Dependency in step '{step_id}' must be a non-empty string.")
                deps.append(d.strip())

            # Input data validation: model output cannot be executable callable
            input_data = raw_step.get("input_data", {})
            if callable(input_data):
                raise ValueError(f"Model-generated input_data for step '{step_id}' cannot be callable.")

            # Task requirements validation
            raw_reqs = raw_step.get("task_requirements")
            task_requirements: TaskRequirements | None = None
            if raw_reqs is not None:
                if not isinstance(raw_reqs, dict):
                    raise ValueError(f"task_requirements for step '{step_id}' must be a dictionary.")

                raw_caps = raw_reqs.get("required_capabilities", [])
                if not isinstance(raw_caps, (list, tuple, set, frozenset)):
                    raise ValueError(f"required_capabilities for step '{step_id}' must be a list of strings.")

                caps = set()
                for c in raw_caps:
                    if not isinstance(c, str) or not c.strip():
                        raise ValueError(f"Capability for step '{step_id}' must be a non-empty string.")
                    c_str = c.strip().lower()
                    try:
                        caps.add(ModelCapability(c_str))
                    except ValueError:
                        caps.add(c_str)

                pref_model = raw_reqs.get("preferred_model")
                if pref_model is not None and (not isinstance(pref_model, str) or not pref_model.strip()):
                    raise ValueError(f"preferred_model for step '{step_id}' must be a non-empty string or None.")

                pref_provider = raw_reqs.get("preferred_provider")
                if pref_provider is not None and (not isinstance(pref_provider, str) or not pref_provider.strip()):
                    raise ValueError(f"preferred_provider for step '{step_id}' must be a non-empty string or None.")

                task_requirements = TaskRequirements(
                    required_capabilities=caps,
                    preferred_model=pref_model.strip() if isinstance(pref_model, str) else None,
                    preferred_provider=pref_provider.strip() if isinstance(pref_provider, str) else None,
                )

            # Metadata validation & untrusted model sanitization
            raw_meta = raw_step.get("metadata", {})
            if raw_meta is None:
                raw_meta = {}
            if not isinstance(raw_meta, dict):
                raise ValueError(f"Metadata for step '{step_id}' must be a dictionary.")

            # Model Plan Safety: Strip any untrusted model-supplied approval/permission claims
            clean_metadata = {
                str(k): v
                for k, v in raw_meta.items()
                if str(k).lower()
                not in (
                    "approved",
                    "approval_status",
                    "is_approved",
                    "auto_approve",
                    "permission",
                    "authorized",
                )
            }

            plan_steps.append(
                PlanStep(
                    step_id=step_id,
                    skill_name=skill_name,
                    input_data=input_data,
                    dependencies=tuple(deps),
                    task_requirements=task_requirements,
                    metadata=clean_metadata,
                )
            )

        return plan_steps

    def plan(
        self,
        task: str,
        task_requirements: TaskRequirements | None = None,
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Generate and validate an ExecutionPlan from a natural-language task description using a model."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("Task description must be a non-empty string.")

        # Resolve active model
        active_model: ModelInterface | None = self.model
        if active_model is None and self.model_router is not None:
            reqs = task_requirements or TaskRequirements(
                required_capabilities=[ModelCapability.REASONING]
            )
            route_res = self.model_router.route(reqs)
            active_model = route_res.provider

        if active_model is None:
            raise ValueError("ModelInterface or ModelRouter required for model-assisted planning.")

        prompt = self._build_planning_prompt(task)
        request_id = uuid4()
        response = active_model.generate(prompt=prompt, request_id=request_id)

        if response is None or not hasattr(response, "content"):
            raise ValueError("Model response is invalid or missing.")

        plan_steps = self._parse_model_plan(response.content)

        p_id = str(plan_id) if plan_id is not None else str(uuid4())
        meta = metadata if metadata is not None else {}
        plan = ExecutionPlan(steps=tuple(plan_steps), plan_id=p_id, metadata=meta)

        self.validate_plan(plan)
        return plan

    def replan(
        self,
        context: ReplanContext,
        task_requirements: TaskRequirements | None = None,
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Generate and validate a replacement ExecutionPlan for an adaptive workflow recovery."""
        if not isinstance(context, ReplanContext):
            raise TypeError("context must be an instance of ReplanContext.")

        active_model: ModelInterface | None = self.model
        if active_model is None and self.model_router is not None:
            reqs = task_requirements or TaskRequirements(
                required_capabilities=[ModelCapability.REASONING]
            )
            route_res = self.model_router.route(reqs)
            active_model = route_res.provider

        if active_model is None:
            raise ValueError("ModelInterface or ModelRouter required for model-assisted replanning.")

        prompt = self._build_replanning_prompt(context)
        request_id = uuid4()
        response = active_model.generate(prompt=prompt, request_id=request_id)

        if response is None or not hasattr(response, "content"):
            raise ValueError("Model response is invalid or missing.")

        plan_steps = self._parse_model_plan(response.content)

        p_id = str(plan_id) if plan_id is not None else str(uuid4())
        meta = dict(metadata) if metadata is not None else {}
        meta["replanned"] = True
        if context.original_plan_id:
            meta["original_plan_id"] = context.original_plan_id

        plan = ExecutionPlan(steps=tuple(plan_steps), plan_id=p_id, metadata=meta)
        self.validate_plan(plan)
        return plan

    def validate_plan(self, plan: ExecutionPlan) -> None:
        """Validate that an execution plan is coherent, complete, and acyclic."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        if not plan.steps:
            raise ValueError("ExecutionPlan must contain at least one step.")

        step_ids: set[str] = set()
        for step in plan.steps:
            if step.step_id in step_ids:
                raise ValueError(f"Duplicate step ID detected: '{step.step_id}'.")
            step_ids.add(step.step_id)

            if not self.skill_registry.has(step.skill_name):
                raise KeyError(
                    f"Unknown skill '{step.skill_name}' required by step '{step.step_id}'."
                )

        for step in plan.steps:
            for dep in step.dependencies:
                if dep == step.step_id:
                    raise ValueError(f"Step '{step.step_id}' cannot depend on itself.")
                if dep not in step_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on nonexistent step '{dep}'."
                    )

        # Cycle detection and topological sort check
        self.get_execution_order(plan)

    def get_execution_order(self, plan: ExecutionPlan) -> list[PlanStep]:
        """Compute a deterministic topological execution order of steps."""
        step_map = {step.step_id: step for step in plan.steps}
        in_degree = {step.step_id: len(step.dependencies) for step in plan.steps}
        dependents: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}

        for step in plan.steps:
            for dep in step.dependencies:
                dependents[dep].append(step.step_id)

        # Deterministic queue: sort initial roots by original insertion order
        queue = [step.step_id for step in plan.steps if in_degree[step.step_id] == 0]
        ordered_ids: list[str] = []

        while queue:
            curr = queue.pop(0)
            ordered_ids.append(curr)

            for nxt in dependents[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(ordered_ids) < len(plan.steps):
            raise ValueError("Circular dependency detected in execution plan.")

        return [step_map[s_id] for s_id in ordered_ids]


    def create_agent_plan(
        self,
        steps: Sequence[AgentPlanStep],
        task_goal: str = "",
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentPlan:
        """Create and validate an immutable AgentPlan from an explicit sequence of steps."""
        p_id = str(plan_id) if plan_id is not None else str(uuid4())
        meta = metadata if metadata is not None else {}
        plan = AgentPlan(
            plan_id=p_id,
            task_goal=task_goal,
            steps=tuple(steps),
            metadata=meta,
        )
        self.validate_agent_plan(plan)
        return plan

    def plan_agent(
        self,
        task: str,
        task_requirements: TaskRequirements | None = None,
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentPlan:
        """Generate and validate an AgentPlan from a task description using a model."""
        exec_plan = self.plan(
            task=task,
            task_requirements=task_requirements,
            plan_id=plan_id,
            metadata=metadata,
        )
        agent_steps = [
            AgentPlanStep(
                step_id=s.step_id,
                skill_name=s.skill_name,
                objective=s.skill_name,
                input_data=s.input_data,
                dependencies=s.dependencies,
                task_requirements=s.task_requirements,
                metadata=dict(s.metadata),
            )
            for s in exec_plan.steps
        ]
        p_id = str(plan_id) if plan_id is not None else exec_plan.plan_id
        agent_plan = AgentPlan(
            plan_id=p_id,
            task_goal=task.strip(),
            steps=tuple(agent_steps),
            metadata=dict(exec_plan.metadata),
        )
        self.validate_agent_plan(agent_plan)
        return agent_plan

    def replan_agent(
        self,
        context: ReplanContext,
        task_requirements: TaskRequirements | None = None,
        plan_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentPlan:
        """Generate and validate a replacement AgentPlan for adaptive workflow recovery."""
        exec_plan = self.replan(
            context=context,
            task_requirements=task_requirements,
            plan_id=plan_id,
            metadata=metadata,
        )
        agent_steps = [
            AgentPlanStep(
                step_id=s.step_id,
                skill_name=s.skill_name,
                objective=s.skill_name,
                input_data=s.input_data,
                dependencies=s.dependencies,
                task_requirements=s.task_requirements,
                metadata=dict(s.metadata),
            )
            for s in exec_plan.steps
        ]
        p_id = str(plan_id) if plan_id is not None else exec_plan.plan_id
        agent_plan = AgentPlan(
            plan_id=p_id,
            task_goal=context.task.strip(),
            steps=tuple(agent_steps),
            metadata=dict(exec_plan.metadata),
        )
        self.validate_agent_plan(agent_plan)
        return agent_plan

    def validate_agent_plan(self, plan: AgentPlan) -> None:
        """Validate that an AgentPlan is coherent, complete, and acyclic."""
        if not isinstance(plan, AgentPlan):
            raise TypeError("plan must be an instance of AgentPlan.")

        if not plan.steps:
            raise ValueError("AgentPlan must contain at least one step.")

        step_ids: set[str] = set()
        for step in plan.steps:
            if step.step_id in step_ids:
                raise ValueError(f"Duplicate step ID detected: '{step.step_id}'.")
            step_ids.add(step.step_id)

            if not self.skill_registry.has(step.skill_name):
                raise KeyError(
                    f"Unknown skill '{step.skill_name}' required by step '{step.step_id}'."
                )

        for step in plan.steps:
            for dep in step.dependencies:
                if dep == step.step_id:
                    raise ValueError(f"Step '{step.step_id}' cannot depend on itself.")
                if dep not in step_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on nonexistent step '{dep}'."
                    )

        # Topological sort check
        self.get_agent_execution_order(plan)

    def get_agent_execution_order(self, plan: AgentPlan) -> list[AgentPlanStep]:
        """Compute a deterministic topological execution order of AgentPlan steps."""
        step_map = {step.step_id: step for step in plan.steps}
        in_degree = {step.step_id: len(step.dependencies) for step in plan.steps}
        dependents: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}

        for step in plan.steps:
            for dep in step.dependencies:
                dependents[dep].append(step.step_id)

        queue = [step.step_id for step in plan.steps if in_degree[step.step_id] == 0]
        ordered_ids: list[str] = []

        while queue:
            curr = queue.pop(0)
            ordered_ids.append(curr)

            for nxt in dependents[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(ordered_ids) < len(plan.steps):
            raise ValueError("Circular dependency detected in execution plan.")

        return [step_map[s_id] for s_id in ordered_ids]

    @staticmethod
    def convert_execution_plan_to_agent_plan(plan: ExecutionPlan, task_goal: str = "") -> AgentPlan:
        """Convert a legacy ExecutionPlan to an immutable AgentPlan."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")
        agent_steps = [
            AgentPlanStep(
                step_id=s.step_id,
                skill_name=s.skill_name,
                objective=s.skill_name,
                input_data=s.input_data,
                dependencies=s.dependencies,
                task_requirements=s.task_requirements,
                metadata=dict(s.metadata),
            )
            for s in plan.steps
        ]
        return AgentPlan(
            plan_id=plan.plan_id,
            task_goal=task_goal,
            steps=tuple(agent_steps),
            metadata=dict(plan.metadata),
        )

    @staticmethod
    def convert_agent_plan_to_execution_plan(plan: AgentPlan) -> ExecutionPlan:
        """Convert an AgentPlan to a legacy ExecutionPlan."""
        if not isinstance(plan, AgentPlan):
            raise TypeError("plan must be an instance of AgentPlan.")
        exec_steps = [
            PlanStep(
                step_id=s.step_id,
                skill_name=s.skill_name,
                input_data=s.input_data,
                dependencies=s.dependencies,
                task_requirements=s.task_requirements,
                metadata=dict(s.metadata),
            )
            for s in plan.steps
        ]
        return ExecutionPlan(
            steps=tuple(exec_steps),
            plan_id=plan.plan_id,
            metadata=dict(plan.metadata),
        )
