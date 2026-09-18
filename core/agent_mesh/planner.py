"""M59 — Structured Planner & ModelGateway Composition.

Generates validated, typed execution plans strictly via M51 ModelGateway.
Ensures model outputs are structured proposals rather than direct execution authority.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.agent_mesh.context import AgentContext
from core.agent_mesh.intent import ClassifiedIntent
from core.agent_mesh.types import (
    ActionType,
    ExecutionPlan,
    PlanStep,
)
from core.cognitive_memory.types import scrub_sensitive_content
from core.platform.types import CapabilityRiskLevel

if TYPE_CHECKING:
    from core.model_gateway import ModelGateway

logger = logging.getLogger("aura.agent_mesh.planner")


class StructuredPlanner:
    """Decomposes goals into structured, policy-checked execution plans."""

    def __init__(self, model_gateway: ModelGateway | Any | None = None):
        self.model_gateway = model_gateway

    def plan(
        self,
        tenant_id: str,
        intent: ClassifiedIntent,
        context: AgentContext,
        max_steps: int = 5,
    ) -> ExecutionPlan:
        """Generate a typed ExecutionPlan (Invariants M59-F12, M59-F15)."""
        # If ModelGateway is active and configured, query it for plan generation
        if self.model_gateway:
            try:
                prompt = self._build_planning_prompt(intent, context, max_steps)
                response = self.model_gateway.generate(
                    prompt=prompt,
                    tenant_id=tenant_id,
                    temperature=0.1,
                    max_tokens=1000,
                )
                raw_text = getattr(response, "text", str(response))
                parsed_plan = self._parse_model_plan(raw_text, intent.cleaned_goal)
                if parsed_plan and len(parsed_plan.steps) > 0:
                    return parsed_plan
            except Exception as e:
                logger.warning(f"ModelGateway plan generation failed for tenant '{tenant_id}': {e}. Using deterministic plan generator.")

        # Deterministic / rule-based plan decomposition
        return self._generate_deterministic_plan(intent, context)

    def _build_planning_prompt(self, intent: ClassifiedIntent, context: AgentContext, max_steps: int) -> str:
        ctx_text = context.to_prompt_text(max_chars=4000)
        return f"""You are the Project AURA Structured Planner. Decompose the following goal into a sequence of maximum {max_steps} typed steps.

CONTEXT:
{ctx_text}

GOAL:
{intent.cleaned_goal}

Output JSON format:
{{
  "steps": [
    {{
      "step_number": 1,
      "action_type": "tool|device|task|delegation|memory|response",
      "action_name": "name_of_action",
      "parameters": {{}},
      "risk_level": "low|medium|high|critical",
      "requires_approval": false,
      "description": "Short explanation"
    }}
  ]
}}
"""

    def _parse_model_plan(self, raw_text: str, goal: str) -> ExecutionPlan | None:
        """Safely parse JSON response from ModelGateway."""
        try:
            cleaned = scrub_sensitive_content(raw_text)
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                return None
            data = json.loads(match.group(0))
            steps_data = data.get("steps", [])
            steps = []
            for s in steps_data:
                action_type_str = s.get("action_type", "response").lower()
                action_type = ActionType.RESPONSE
                try:
                    action_type = ActionType(action_type_str)
                except ValueError:
                    action_type = ActionType.RESPONSE

                risk_str = s.get("risk_level", "low").lower()
                risk = CapabilityRiskLevel.LOW
                try:
                    risk = CapabilityRiskLevel(risk_str)
                except ValueError:
                    risk = CapabilityRiskLevel.LOW

                steps.append(
                    PlanStep(
                        step_number=int(s.get("step_number", len(steps) + 1)),
                        action_type=action_type,
                        action_name=s.get("action_name", "respond"),
                        parameters=dict(s.get("parameters", {})),
                        risk_level=risk,
                        requires_approval=bool(s.get("requires_approval", False) or risk in (CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL)),
                        description=s.get("description", ""),
                    )
                )
            return ExecutionPlan(goal=goal, steps=steps)
        except Exception:
            return None

    def _generate_deterministic_plan(self, intent: ClassifiedIntent, context: AgentContext) -> ExecutionPlan:
        """Deterministic plan fallback for unit testing and offline execution."""
        steps = []
        if intent.requires_device_interaction:
            # Check if intent is destructive
            if intent.estimated_risk == CapabilityRiskLevel.HIGH:
                steps.append(
                    PlanStep(
                        step_number=1,
                        action_type=ActionType.DEVICE,
                        action_name="delete_sandboxed_file",
                        parameters={"path": "target.txt"},
                        risk_level=CapabilityRiskLevel.HIGH,
                        requires_approval=True,
                        description="Delete target file inside sandbox after human approval",
                    )
                )
            else:
                steps.append(
                    PlanStep(
                        step_number=1,
                        action_type=ActionType.DEVICE,
                        action_name="get_system_info",
                        parameters={},
                        risk_level=CapabilityRiskLevel.LOW,
                        requires_approval=False,
                        description="Query system info via M58 Desktop Adapter",
                    )
                )
        elif intent.requires_multimodal:
            steps.append(
                PlanStep(
                    step_number=1,
                    action_type=ActionType.MULTIMODAL,
                    action_name="process_artifact",
                    parameters={"modality": "image"},
                    risk_level=CapabilityRiskLevel.LOW,
                    requires_approval=False,
                    description="Process multimodal input via M57",
                )
            )
        else:
            steps.append(
                PlanStep(
                    step_number=1,
                    action_type=ActionType.RESPONSE,
                    action_name="generate_response",
                    parameters={"message": f"Processed request: {intent.cleaned_goal}"},
                    risk_level=CapabilityRiskLevel.LOW,
                    requires_approval=False,
                    description="Synthesize direct response for user",
                )
            )

        return ExecutionPlan(goal=intent.cleaned_goal, steps=steps)
