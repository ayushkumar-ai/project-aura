"""Agentic Self-Reflection Engine (M14).

Performs diagnostic analysis of executed plans, traces, and observations.
Identifies root causes, failure modes, retry/replan efficacy, and distills
actionable heuristic rules and semantic facts while preserving provenance.
Non-authorizing: strictly strips permission/approval override keys.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from app.config import settings
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepStatus,
)
from core.provenance import (
    TaintedValue,
    is_tainted,
    render_for_prompt,
    unwrap_tainted,
    wrap_tainted,
)
from core.reflection_types import (
    CritiqueSeverity,
    FailureIssueType,
    ReflectionAssessment,
    ReflectionRecord,
    ReflectionRule,
    StepCritique,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.agent_reflection")


def diagnose_failure_issue_type(error_msg: str | None) -> FailureIssueType:
    """Deterministically categorize a failure message into a structured FailureIssueType."""
    if not error_msg:
        return FailureIssueType.NONE

    err_lower = error_msg.lower()
    if any(k in err_lower for k in ("policy", "denied", "permission", "unauthorized", "security boundary")):
        return FailureIssueType.POLICY_DENIAL
    if any(k in err_lower for k in ("timeout", "timed out", "time limit")):
        return FailureIssueType.TIMEOUT
    if any(k in err_lower for k in ("dependency", "prerequisite", "missing dependency")):
        return FailureIssueType.DEPENDENCY_FAILURE
    if any(k in err_lower for k in ("resource limit", "quota", "rate limit", "exceeded maximum")):
        return FailureIssueType.RESOURCE_LIMIT_EXCEEDED
    if any(k in err_lower for k in ("validation error", "invalid parameter", "missing required", "typeerror", "valueerror", "payload")):
        return FailureIssueType.TOOL_PAYLOAD_ERROR
    if any(k in err_lower for k in ("tool error", "execution failed", "failed to execute", "command failed")):
        return FailureIssueType.TOOL_EXECUTION_FAILURE

    return FailureIssueType.UNKNOWN


def format_reflection_for_prompt(
    reflection: ReflectionRecord | ReflectionAssessment | None,
    max_chars: int = 2000,
) -> str:
    """Format reflection assessment and distilled rules safely for LLM prompt injection."""
    if reflection is None:
        return ""

    assessment: ReflectionAssessment
    if isinstance(reflection, ReflectionRecord):
        assessment = reflection.assessment
    elif isinstance(reflection, ReflectionAssessment):
        assessment = reflection
    else:
        return ""

    lines: list[str] = ["[Prior Execution Reflection & Distilled Guidance]"]
    lines.append(f"- Outcome: {'Success' if assessment.success else 'Failed'} (Efficiency: {assessment.efficiency_score:.2f})")
    if assessment.failure_issue_type != FailureIssueType.NONE:
        lines.append(f"- Failure Category: {assessment.failure_issue_type.value}")
    if assessment.root_cause:
        lines.append(f"- Root Cause: {assessment.root_cause}")

    if assessment.lessons_learned:
        lines.append("- Lessons Learned:")
        for lesson in assessment.lessons_learned[:5]:
            lines.append(f"  * {lesson}")

    if assessment.rules_distilled:
        lines.append("- Distilled Actionable Rules:")
        for rule in assessment.rules_distilled[:5]:
            guidance_text = render_for_prompt(rule.guidance)
            lines.append(f"  * When: {rule.trigger_condition} -> Guidance: {guidance_text} (conf: {rule.confidence:.2f})")

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars - 3] + "..."
    return result


class AgentReflector:
    """Self-reflection engine for post-execution critique and heuristic distillation."""

    def __init__(
        self,
        max_reflection_chars: int | None = None,
        max_reflection_passes: int | None = None,
        llm_provider: Any | None = None,
    ):
        self.max_reflection_chars = (
            max_reflection_chars
            if max_reflection_chars is not None
            else getattr(settings, "aura_max_reflection_chars", 2000)
        )
        self.max_reflection_passes = (
            max_reflection_passes
            if max_reflection_passes is not None
            else getattr(settings, "aura_max_reflection_passes", 1)
        )
        self.llm_provider = llm_provider

    def critique_step(
        self,
        step: AgentPlanStep,
        observation: Observation | None = None,
    ) -> StepCritique:
        """Evaluate a single executed plan step and generate a StepCritique."""
        obs = observation or step.result
        is_success = step.status == StepStatus.SUCCEEDED and (obs is None or obs.success)
        err = obs.error if obs and obs.error else (step.metadata.get("error") if step.metadata else None)

        issue_type = None
        severity = CritiqueSeverity.INFO
        diagnosis = "Step executed successfully."
        remedy = None
        is_untrusted = False

        if not is_success:
            issue = diagnose_failure_issue_type(err)
            issue_type = issue.value
            severity = CritiqueSeverity.CRITICAL if issue in (FailureIssueType.POLICY_DENIAL, FailureIssueType.TIMEOUT) else CritiqueSeverity.WARNING
            diagnosis = f"Step failed during execution: {err or 'Unknown failure'}"
            if issue == FailureIssueType.TOOL_PAYLOAD_ERROR:
                remedy = f"Validate and correct input payload schema for skill '{step.skill_name}' before retrying."
            elif issue == FailureIssueType.TIMEOUT:
                remedy = f"Increase execution timeout or decompose '{step.skill_name}' into smaller granular sub-tasks."
            elif issue == FailureIssueType.POLICY_DENIAL:
                remedy = "Request user approval or adjust tool arguments to conform to security policy."
            elif issue == FailureIssueType.DEPENDENCY_FAILURE:
                remedy = "Ensure all prerequisite step outputs are available and valid before executing."
            else:
                remedy = f"Check skill '{step.skill_name}' logs and retry with alternate parameters."

        if obs is not None:
            is_untrusted = obs.is_untrusted
            if is_tainted(obs.output):
                is_untrusted = True

        # Compute step efficiency score based on retries and execution time
        eff = 1.0
        if not is_success:
            eff = 0.0
        elif step.retry_count > 0:
            eff = max(0.1, 1.0 - (step.retry_count * 0.25))

        return StepCritique(
            step_id=step.step_id,
            skill_name=step.skill_name,
            success=is_success,
            efficiency_score=eff,
            issue_type=issue_type,
            severity=severity,
            diagnosis=diagnosis,
            remedy_suggestion=remedy,
            is_untrusted=is_untrusted,
            metadata={"retry_count": step.retry_count, "execution_time_ms": obs.execution_time_ms if obs else 0.0},
        )

    def reflect_on_execution(
        self,
        plan: AgentPlan,
        trace: ExecutionTrace,
        goal: Any | None = None,
        task_id: str | None = None,
    ) -> ReflectionRecord:
        """Perform a comprehensive reflection pass over an executed plan and its execution trace."""
        t_id = task_id or plan.metadata.get("task_id") or trace.task_id or f"task-{uuid.uuid4().hex[:8]}"
        plan_id = plan.plan_id
        trace_id = trace.trace_id

        # Map observations by step_id
        obs_by_step: dict[str, list[Observation]] = {}
        for obs in trace.observations:
            obs_by_step.setdefault(obs.step_id, []).append(obs)

        critiques: list[StepCritique] = []
        failed_steps: list[AgentPlanStep] = []
        retried_steps = 0
        was_retry_useful = False
        untrusted_trace = False

        for step in plan.steps:
            obs_list = obs_by_step.get(step.step_id, [])
            last_obs = obs_list[-1] if obs_list else step.result
            critique = self.critique_step(step, last_obs)
            critiques.append(critique)

            if critique.is_untrusted:
                untrusted_trace = True

            if step.retry_count > 0:
                retried_steps += step.retry_count
                if step.status == StepStatus.SUCCEEDED:
                    was_retry_useful = True

            if step.status in (StepStatus.FAILED, StepStatus.BLOCKED) or not critique.success:
                failed_steps.append(step)

        success = plan.is_completed() and len(failed_steps) == 0
        replan_count = len(trace.replan_history)
        was_replan_useful = replan_count > 0 and success

        # Efficiency calculation
        total_steps = max(1, len(plan.steps))
        succeeded_steps = total_steps - len(failed_steps)
        raw_eff = (succeeded_steps / total_steps) - (retried_steps * 0.1) - (replan_count * 0.05)
        efficiency_score = max(0.0, min(1.0, raw_eff)) if success else max(0.0, min(0.5, raw_eff))

        # Root cause and failure issue type
        failure_issue_type = FailureIssueType.NONE
        root_cause = None
        if not success:
            if failed_steps:
                primary_failure = failed_steps[0]
                last_obs = obs_by_step.get(primary_failure.step_id, [None])[-1] or primary_failure.result
                err_msg = last_obs.error if last_obs and last_obs.error else primary_failure.metadata.get("error")
                failure_issue_type = diagnose_failure_issue_type(err_msg)
                root_cause = f"Step '{primary_failure.step_id}' ({primary_failure.skill_name}) failed: {err_msg or 'Execution incomplete'}"
            else:
                failure_issue_type = FailureIssueType.UNKNOWN
                root_cause = "Plan terminated with incomplete or blocked steps."

        # Lessons and rule distillation
        lessons: list[str] = []
        rules: list[ReflectionRule] = []
        heuristics: list[str] = []

        if success:
            summary = f"Execution succeeded with {len(plan.steps)} steps (efficiency: {efficiency_score:.2f})."
            lessons.append(f"Workflow '{plan.task_goal or 'task'}' executed successfully with skills {[s.skill_name for s in plan.steps]}.")
            if was_retry_useful:
                lessons.append("Transient failures were resolved via step retries.")
            if was_replan_useful:
                lessons.append(f"Dynamic replanning ({replan_count} replans) recovered the workflow to success.")
        else:
            summary = f"Execution failed at {root_cause} (issue: {failure_issue_type.value})."
            lessons.append(f"Failure in skill '{failed_steps[0].skill_name if failed_steps else 'unknown'}': {root_cause}")

        # Distill actionable rules
        for sc in critiques:
            if not sc.success and sc.remedy_suggestion:
                rule_id = f"rule-{uuid.uuid4().hex[:8]}"
                guidance_val: str | TaintedValue = sc.remedy_suggestion
                if sc.is_untrusted:
                    guidance_val = wrap_tainted(sc.remedy_suggestion, is_untrusted=True, source_type="untrusted_trace")

                rule = ReflectionRule(
                    rule_id=rule_id,
                    trigger_condition=f"Failure in '{sc.skill_name}' ({sc.issue_type or 'execution_error'})",
                    guidance=guidance_val,
                    confidence=0.85,
                    source_trace_id=trace_id,
                    tags=[sc.skill_name, sc.issue_type or "general_failure"],
                )
                rules.append(rule)
                heuristics.append(f"When using '{sc.skill_name}': {sc.remedy_suggestion}")
            elif sc.success and sc.efficiency_score > 0.8:
                heuristics.append(f"Skill '{sc.skill_name}' succeeded efficiently for objective in step {sc.step_id}.")

        assessment = ReflectionAssessment(
            success=success,
            efficiency_score=efficiency_score,
            steps_executed=len(trace.observations) or len(plan.steps),
            steps_failed=len(failed_steps),
            retried_steps_count=retried_steps,
            replan_count=replan_count,
            failure_issue_type=failure_issue_type,
            root_cause=root_cause,
            was_retry_useful=was_retry_useful,
            was_replan_useful=was_replan_useful,
            lessons_learned=lessons,
            rules_distilled=rules,
            step_critiques=critiques,
            summary=summary,
            metadata={"untrusted_trace": untrusted_trace},
        )

        goal_id = getattr(goal, "goal_id", None) if goal else None

        record = ReflectionRecord(
            reflection_id=f"refl-{uuid.uuid4().hex[:12]}",
            target_id=t_id,
            target_type="plan",
            assessment=assessment,
            trace_id=trace_id,
            plan_id=plan_id,
            goal_id=goal_id,
            heuristics_distilled=heuristics,
            timestamp=time.time(),
            metadata={"plan_goal": plan.task_goal},
        )
        return record
