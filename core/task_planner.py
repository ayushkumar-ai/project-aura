import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.capability_registry import ModelCapability
from core.model_router import ModelRouter, TaskRequirements
from core.skill_registry import SkillRegistry
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


class TaskPlanner:
    """Creates, generates, and validates deterministic execution plans."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        model: ModelInterface | None = None,
        model_router: ModelRouter | None = None,
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

        return (
            "You are a task planner in AURA.\n"
            "Decompose the following task into an execution plan using ONLY the available skills.\n\n"
            f"Available Skills:\n{skills_text}\n\n"
            f"Task to accomplish:\n{task.strip()}\n\n"
            "Output the execution plan as a JSON object with a 'steps' list where each step has:\n"
            "- 'step_id': unique string (e.g. 'step_1')\n"
            "- 'skill_name': name of an available skill\n"
            "- 'input_data': input payload for the skill (optional dict or value)\n"
            "- 'dependencies': list of prerequisite step_ids (can be empty)\n\n"
            "Respond ONLY with valid JSON."
        )

    def _parse_model_plan(self, raw_content: str) -> list[PlanStep]:
        """Parse model output text into PlanStep objects."""
        content = raw_content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        try:
            data = json.loads(content)
        except Exception as e:
            raise ValueError(f"Failed to parse model plan output as JSON: {e}")

        if isinstance(data, dict):
            raw_steps = data.get("steps")
            if raw_steps is None:
                raise ValueError("Model output JSON missing 'steps' key.")
        elif isinstance(data, list):
            raw_steps = data
        else:
            raise ValueError("Model output must be a JSON object with 'steps' or a JSON list of steps.")

        if not isinstance(raw_steps, list):
            raise ValueError("'steps' must be a list in model output.")

        if not raw_steps:
            raise ValueError("Model generated an empty list of steps.")

        plan_steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise ValueError("Each step in model output must be a JSON object/dictionary.")

            step_id = raw_step.get("step_id")
            skill_name = raw_step.get("skill_name")

            if not step_id or not isinstance(step_id, str):
                raise ValueError("Each step must have a valid non-empty 'step_id'.")
            if not skill_name or not isinstance(skill_name, str):
                raise ValueError("Each step must have a valid non-empty 'skill_name'.")

            input_data = raw_step.get("input_data", {})
            dependencies = raw_step.get("dependencies", [])
            metadata = raw_step.get("metadata", {})

            if not isinstance(dependencies, (list, tuple)):
                raise ValueError(f"Dependencies for step '{step_id}' must be a list.")
            if not isinstance(metadata, dict):
                raise ValueError(f"Metadata for step '{step_id}' must be a dictionary.")

            plan_steps.append(
                PlanStep(
                    step_id=step_id,
                    skill_name=skill_name,
                    input_data=input_data,
                    dependencies=tuple(dependencies),
                    metadata=metadata,
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

        plan_steps = self._parse_model_plan(response.content)

        p_id = str(plan_id) if plan_id is not None else str(uuid4())
        meta = metadata if metadata is not None else {}
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
