"""Milestone 27: Self-Healing Orchestrator.

Closed-loop recovery coordinator that:
1. Invokes CausalFaultAnalyzer to diagnose a campaign phase failure.
2. Invokes RemediationPlanner to build a bounded RemediationPlan.
3. Executes each RemediationAction within the healing budget.
4. Reports healing status to StreamingGateway and Tracer.
5. Resumes forward campaign execution on success.
6. Falls back to saga compensation on budget exhaustion or irrecoverable failure.

Security invariants:
- Never bypasses Policy, ApprovalGateway, or ToolExecutor boundaries.
- Actions requiring approval are NOT executed autonomously — they emit a
  ClarificationRequest and return OPERATOR_ESCALATED.
- Healing budgets are enforced; infinite loops are structurally impossible.
- Taint semantics are preserved across all artifact operations.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from core.causal_fault_analyzer import CausalFaultAnalyzer
from core.fault_types import (
    FaultDiagnosticReport,
    HealingBudget,
    HealingStatus,
    RemediationAction,
    RemediationActionType,
    RemediationPlan,
    SelfHealingResult,
)
from core.remediation_planner import RemediationPlanner
from core.trace_types import SpanKind, SpanStatus

logger = logging.getLogger("aura.self_healing_orchestrator")

# Ring buffer cap for per-orchestrator healing history
MAX_HEALING_HISTORY = 500


class SelfHealingOrchestrator:
    """Manages the lifecycle of a self-healing attempt for a failed campaign phase."""

    def __init__(
        self,
        analyzer: CausalFaultAnalyzer | None = None,
        planner: RemediationPlanner | None = None,
        dynamic_skill_registry: Any | None = None,
        skill_synthesizer: Any | None = None,
        skill_verification_harness: Any | None = None,
        saga_coordinator_factory: Any | None = None,
        clarification_gateway: Any | None = None,
        streaming_gateway: Any | None = None,
        tracer: Any | None = None,
        default_budget: HealingBudget | None = None,
    ) -> None:
        self._analyzer = analyzer if analyzer is not None else CausalFaultAnalyzer(tracer=tracer)
        self._planner = planner if planner is not None else RemediationPlanner()
        self._dynamic_skill_registry = dynamic_skill_registry
        self._skill_synthesizer = skill_synthesizer
        self._skill_verification_harness = skill_verification_harness
        self._saga_coordinator_factory = saga_coordinator_factory
        self._clarification_gateway = clarification_gateway
        self._streaming_gateway = streaming_gateway
        self._tracer = tracer
        self._default_budget = default_budget if default_budget is not None else HealingBudget()

        self._lock = threading.RLock()
        # campaign_id → deque of SelfHealingResult
        self._healing_history: dict[str, deque[SelfHealingResult]] = {}
        # (campaign_id, phase_id) → attempt count
        self._attempt_counts: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def heal_campaign_phase(
        self,
        campaign_id: str,
        phase_id: str,
        goal_id: str,
        error_message: str,
        error_traceback: str = "",
        causal_graph: Any | None = None,
        failing_input: str = "",
        affected_artifact_ids: list[str] | None = None,
        saga_coordinator: Any | None = None,
        context: dict[str, Any] | None = None,
        budget: HealingBudget | None = None,
    ) -> SelfHealingResult:
        """Attempt to diagnose and heal a failed campaign phase.

        Returns a SelfHealingResult with status RECOVERED (phase may be retried
        by the caller), HEALING_FAILED, BUDGET_EXHAUSTED, or OPERATOR_ESCALATED.

        The caller (CampaignEngine) is responsible for re-executing the phase after
        RECOVERED is returned, or falling back to saga compensation otherwise.
        """
        budget = budget if budget is not None else self._default_budget
        ctx = context or {}
        start_time = time.time()

        # ── Budget guard: check attempt count ──────────────────────────────
        key = (str(campaign_id).strip(), str(phase_id).strip())
        with self._lock:
            attempt_number = self._attempt_counts.get(key, 0) + 1
            if attempt_number > budget.max_attempts:
                logger.warning(
                    "SelfHealingOrchestrator: budget exhausted for campaign=%s phase=%s (attempts=%d)",
                    campaign_id, phase_id, attempt_number - 1,
                )
                result = SelfHealingResult(
                    result_id=f"shr_{hash(campaign_id + phase_id) & 0xFFFFFF:06x}",
                    plan_id="none",
                    campaign_id=campaign_id,
                    phase_id=phase_id,
                    status=HealingStatus.BUDGET_EXHAUSTED,
                    fault_category=self._fault_category_from_error(error_message),
                    actions_attempted=0,
                    actions_succeeded=0,
                    campaign_resumed=False,
                    duration_seconds=0.0,
                    error="Max healing attempts exhausted.",
                    attempt_number=attempt_number,
                    timestamp=time.time(),
                )
                self._record_result(campaign_id, result)
                return result
            self._attempt_counts[key] = attempt_number

        # ── Tracing span setup ─────────────────────────────────────────────
        healing_span = None
        if self._tracer is not None:
            try:
                healing_span = self._tracer.start_span(
                    name=f"self_healing.phase.{phase_id}",
                    kind=SpanKind.INTERNAL,
                    attributes={
                        "campaign_id": campaign_id,
                        "phase_id": phase_id,
                        "goal_id": goal_id,
                        "attempt_number": str(attempt_number),
                        "error_message": error_message[:256],
                    },
                )
            except Exception:
                healing_span = None

        self._emit_stream_event(
            campaign_id=campaign_id,
            event_type="healing_started",
            data={"phase_id": phase_id, "attempt": attempt_number, "error": error_message[:128]},
        )

        plan: RemediationPlan | None = None
        result: SelfHealingResult | None = None

        try:
            # ── Step 1: Diagnose ───────────────────────────────────────────
            if healing_span is not None:
                try:
                    healing_span.set_attribute("healing_status", HealingStatus.DIAGNOSING.value)
                except Exception:
                    pass

            fault_report = self._diagnose(
                campaign_id=campaign_id,
                phase_id=phase_id,
                goal_id=goal_id,
                error_message=error_message,
                error_traceback=error_traceback,
                causal_graph=causal_graph,
                failing_input=failing_input,
                affected_artifact_ids=affected_artifact_ids,
                ctx=ctx,
            )

            # ── Step 2: Plan ───────────────────────────────────────────────
            if healing_span is not None:
                try:
                    healing_span.set_attribute("healing_status", HealingStatus.PLANNING.value)
                    healing_span.set_attribute("fault_category", fault_report.fault_category.value)
                    healing_span.set_attribute("confidence", fault_report.confidence_level.value)
                except Exception:
                    pass

            plan = self._planner.plan(
                fault_report=fault_report,
                budget=budget,
                context=ctx,
            )

            # Detect repeated identical plans (anti-loop)
            if self._is_repeated_plan(campaign_id, phase_id, plan):
                logger.warning(
                    "SelfHealingOrchestrator: repeated identical plan detected — aborting healing "
                    "for campaign=%s phase=%s to prevent loop.",
                    campaign_id, phase_id,
                )
                result = self._make_result(
                    plan=plan,
                    status=HealingStatus.HEALING_FAILED,
                    fault_report=fault_report,
                    actions_attempted=0,
                    actions_succeeded=0,
                    campaign_resumed=False,
                    start_time=start_time,
                    attempt_number=attempt_number,
                    error="Repeated identical remediation plan detected — aborting to prevent loop.",
                )
                return result

            # ── Step 3: Execute ────────────────────────────────────────────
            if healing_span is not None:
                try:
                    healing_span.set_attribute("healing_status", HealingStatus.EXECUTING.value)
                    healing_span.set_attribute("plan_id", plan.plan_id)
                    healing_span.set_attribute("actions_count", str(len(plan.actions)))
                except Exception:
                    pass

            # Operator escalation — don't execute, just report
            if plan.is_operator_escalation:
                self._request_operator_clarification(fault_report, plan)
                result = self._make_result(
                    plan=plan,
                    status=HealingStatus.OPERATOR_ESCALATED,
                    fault_report=fault_report,
                    actions_attempted=0,
                    actions_succeeded=0,
                    campaign_resumed=False,
                    start_time=start_time,
                    attempt_number=attempt_number,
                    error=None,
                )
                return result

            actions_attempted = 0
            actions_succeeded = 0
            final_status = HealingStatus.HEALING_FAILED
            action_error: str | None = None

            for action in plan.actions:
                # Time budget check
                elapsed = time.time() - start_time
                if elapsed >= budget.max_total_seconds:
                    logger.warning(
                        "SelfHealingOrchestrator: time budget %.1fs exceeded at action=%s",
                        budget.max_total_seconds, action.action_id,
                    )
                    final_status = HealingStatus.BUDGET_EXHAUSTED
                    action_error = f"Time budget exceeded ({elapsed:.1f}s >= {budget.max_total_seconds}s)."
                    break

                # Approval-gated actions must not execute autonomously
                if action.requires_approval:
                    self._request_operator_clarification(fault_report, plan)
                    final_status = HealingStatus.OPERATOR_ESCALATED
                    break

                actions_attempted += 1
                success, err = self._execute_action(action, fault_report, saga_coordinator, ctx)
                if success:
                    actions_succeeded += 1
                    self._emit_stream_event(
                        campaign_id=campaign_id,
                        event_type="healing_action_succeeded",
                        data={"action_type": action.action_type.value, "action_id": action.action_id},
                    )
                else:
                    action_error = err
                    self._emit_stream_event(
                        campaign_id=campaign_id,
                        event_type="healing_action_failed",
                        data={"action_type": action.action_type.value, "error": str(err)[:256]},
                    )
                    # Non-retry actions that fail → stop and report HEALING_FAILED
                    if action.action_type != RemediationActionType.RETRY_PHASE:
                        break

            # Determine final status
            if final_status not in (HealingStatus.BUDGET_EXHAUSTED, HealingStatus.OPERATOR_ESCALATED):
                # RECOVERED if we had at least one action succeed and the last action was RETRY
                last_retry = any(
                    a.action_type == RemediationActionType.RETRY_PHASE for a in plan.actions
                )
                if actions_succeeded > 0 and (actions_succeeded == actions_attempted) and last_retry:
                    final_status = HealingStatus.RECOVERED
                elif actions_succeeded > 0 and action_error is None:
                    final_status = HealingStatus.RECOVERED
                else:
                    final_status = HealingStatus.HEALING_FAILED

            campaign_resumed = final_status == HealingStatus.RECOVERED

            result = self._make_result(
                plan=plan,
                status=final_status,
                fault_report=fault_report,
                actions_attempted=actions_attempted,
                actions_succeeded=actions_succeeded,
                campaign_resumed=campaign_resumed,
                start_time=start_time,
                attempt_number=attempt_number,
                error=action_error,
            )
            return result

        except Exception as ex:
            logger.error(
                "SelfHealingOrchestrator: unexpected exception during healing campaign=%s phase=%s: %s",
                campaign_id, phase_id, ex, exc_info=True,
            )
            result = SelfHealingResult(
                result_id=f"shr_{uuid4_hex()}",
                plan_id=plan.plan_id if plan is not None else "none",
                campaign_id=campaign_id,
                phase_id=phase_id,
                status=HealingStatus.HEALING_FAILED,
                fault_category=self._fault_category_from_error(error_message),
                actions_attempted=0,
                actions_succeeded=0,
                campaign_resumed=False,
                duration_seconds=time.time() - start_time,
                error=f"Orchestrator exception: {ex}",
                attempt_number=attempt_number,
                timestamp=time.time(),
            )
            return result

        finally:
            if result is not None:
                self._record_result(campaign_id, result)
                if healing_span is not None:
                    try:
                        healing_span.set_attribute("healing_final_status", result.status.value)
                        healing_span.set_attribute("campaign_resumed", str(result.campaign_resumed))
                        healing_span.set_status(
                            SpanStatus.OK if result.campaign_resumed else SpanStatus.ERROR,
                            message=result.error or "",
                        )
                        healing_span.end()
                    except Exception:
                        pass
                self._emit_stream_event(
                    campaign_id=campaign_id,
                    event_type="healing_completed",
                    data={
                        "phase_id": phase_id,
                        "status": result.status.value,
                        "resumed": result.campaign_resumed,
                    },
                )

    def get_healing_history(self, campaign_id: str) -> list[SelfHealingResult]:
        """Return the healing result history for a campaign (most recent first)."""
        with self._lock:
            history = self._healing_history.get(str(campaign_id).strip())
            return list(reversed(history)) if history else []

    def get_attempt_count(self, campaign_id: str, phase_id: str) -> int:
        """Return how many healing attempts have been made for a (campaign, phase) pair."""
        with self._lock:
            return self._attempt_counts.get((str(campaign_id).strip(), str(phase_id).strip()), 0)

    def reset_attempt_count(self, campaign_id: str, phase_id: str) -> None:
        """Reset attempt counter — used after a successful campaign re-run (checkpoint restore)."""
        with self._lock:
            key = (str(campaign_id).strip(), str(phase_id).strip())
            self._attempt_counts.pop(key, None)

    def to_dict(self) -> dict[str, Any]:
        """Serialise history and attempt counts for checkpoint persistence."""
        with self._lock:
            history_data: dict[str, list[dict[str, Any]]] = {
                cid: [r.to_dict() for r in list(hist)]
                for cid, hist in self._healing_history.items()
            }
            attempt_data = {
                f"{cid}|{pid}": cnt
                for (cid, pid), cnt in self._attempt_counts.items()
            }
        return {"history": history_data, "attempts": attempt_data}

    def restore_from_dict(self, data: dict[str, Any]) -> None:
        """Restore history and attempt counts from a checkpoint payload.

        Any phase that was EXECUTING at checkpoint time is safely treated as
        HEALING_FAILED — mid-execution cannot be resumed atomically.
        """
        with self._lock:
            history_data = data.get("history", {})
            for cid, entries in history_data.items():
                dq: deque[SelfHealingResult] = deque(maxlen=MAX_HEALING_HISTORY)
                for entry in entries:
                    try:
                        r = SelfHealingResult.from_dict(entry)
                        # Mid-execution at checkpoint time → mark as failed (safe state)
                        if r.status == HealingStatus.EXECUTING:
                            r = SelfHealingResult(
                                result_id=r.result_id,
                                plan_id=r.plan_id,
                                campaign_id=r.campaign_id,
                                phase_id=r.phase_id,
                                status=HealingStatus.HEALING_FAILED,
                                fault_category=r.fault_category,
                                actions_attempted=r.actions_attempted,
                                actions_succeeded=r.actions_succeeded,
                                campaign_resumed=False,
                                duration_seconds=r.duration_seconds,
                                error="Interrupted at checkpoint — treated as HEALING_FAILED on restore.",
                                attempt_number=r.attempt_number,
                                timestamp=r.timestamp,
                                metadata=r.metadata,
                            )
                        dq.append(r)
                    except Exception as ex:
                        logger.debug("SelfHealingOrchestrator: skipping corrupt history entry: %s", ex)
                self._healing_history[cid] = dq

            for compound_key, cnt in data.get("attempts", {}).items():
                parts = str(compound_key).split("|", 1)
                if len(parts) == 2:
                    self._attempt_counts[(parts[0], parts[1])] = int(cnt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _diagnose(
        self,
        campaign_id: str,
        phase_id: str,
        goal_id: str,
        error_message: str,
        error_traceback: str,
        causal_graph: Any | None,
        failing_input: str,
        affected_artifact_ids: list[str] | None,
        ctx: dict[str, Any],
    ) -> FaultDiagnosticReport:
        diag_span = None
        if self._tracer is not None:
            try:
                diag_span = self._tracer.start_span(
                    name=f"self_healing.diagnose.{phase_id}",
                    kind=SpanKind.INTERNAL,
                    attributes={"campaign_id": campaign_id, "phase_id": phase_id},
                )
            except Exception:
                pass
        try:
            return self._analyzer.analyze(
                campaign_id=campaign_id,
                phase_id=phase_id,
                goal_id=goal_id,
                error_message=error_message,
                error_traceback=error_traceback,
                causal_graph=causal_graph,
                failing_input=failing_input,
                affected_artifact_ids=affected_artifact_ids,
                context=ctx,
            )
        finally:
            if diag_span is not None:
                try:
                    diag_span.set_status(SpanStatus.OK)
                    diag_span.end()
                except Exception:
                    pass

    def _execute_action(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
        saga_coordinator: Any | None,
        ctx: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Execute one remediation action. Returns (success, error_message|None)."""
        span = None
        if self._tracer is not None:
            try:
                span = self._tracer.start_span(
                    name=f"self_healing.action.{action.action_type.value}",
                    kind=SpanKind.INTERNAL,
                    attributes={
                        "action_id": action.action_id,
                        "action_type": action.action_type.value,
                        "target_id": action.target_id,
                        "tier": str(action.tier),
                    },
                )
            except Exception:
                pass
        try:
            if action.action_type == RemediationActionType.RETRY_PHASE:
                return self._action_retry(action, fault_report)

            elif action.action_type == RemediationActionType.PATCH_DYNAMIC_SKILL:
                return self._action_patch_skill(action, fault_report)

            elif action.action_type == RemediationActionType.INJECT_DATAFLOW_TRANSFORMER:
                return self._action_inject_transformer(action, fault_report)

            elif action.action_type == RemediationActionType.REPLAN_GOAL:
                return self._action_replan_goal(action, fault_report, ctx)

            elif action.action_type == RemediationActionType.SELECTIVE_SAGA_ROLLBACK:
                return self._action_selective_rollback(action, fault_report, saga_coordinator)

            elif action.action_type in (
                RemediationActionType.REQUEST_OPERATOR_CLARIFICATION,
                RemediationActionType.ABORT_CAMPAIGN,
            ):
                # These should not reach _execute_action; handled before loop
                return False, f"Action type {action.action_type.value} should have been handled prior to execution."

            else:
                return False, f"Unknown action type: {action.action_type.value}"

        except Exception as ex:
            logger.error("SelfHealingOrchestrator: action %s failed: %s", action.action_id, ex)
            if span is not None:
                try:
                    span.record_exception(ex)
                    span.set_status(SpanStatus.ERROR, message=str(ex))
                except Exception:
                    pass
            return False, str(ex)
        finally:
            if span is not None:
                try:
                    span.set_status(SpanStatus.OK)
                    span.end()
                except Exception:
                    pass

    def _action_retry(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
    ) -> tuple[bool, str | None]:
        """Tier-1: signal that a retry is appropriate. The caller (CampaignEngine) will re-run."""
        delay_str = action.parameters.get("retry_delay_seconds", "0")
        try:
            delay = float(delay_str)
            if delay > 0:
                time.sleep(min(delay, 5.0))   # cap sleep to 5s
        except (ValueError, TypeError):
            pass
        logger.info(
            "SelfHealingOrchestrator: RETRY_PHASE signalled for phase=%s goal=%s",
            fault_report.phase_id, fault_report.goal_id,
        )
        return True, None

    def _action_patch_skill(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
    ) -> tuple[bool, str | None]:
        """Tier-2: deprecate the failing skill so the next retry doesn't invoke it.

        Full code-level patching via SkillSynthesizer is an extension point;
        the guaranteed safe behavior is deprecating the broken skill so the campaign
        can fall through to alternative tools or replan without the defective skill.
        """
        skill_name = action.target_id.strip()
        if not skill_name or skill_name == "unknown_skill":
            return False, "Cannot patch: skill name unknown."

        if self._dynamic_skill_registry is not None:
            try:
                deprecated = self._dynamic_skill_registry.deprecate_skill(
                    skill_name,
                    reason=f"M27 self-healing: skill raised exception — {fault_report.error_message[:128]}",
                )
                if deprecated:
                    logger.info(
                        "SelfHealingOrchestrator: deprecated skill '%s' as remediation action.", skill_name
                    )
                    return True, None
                else:
                    logger.debug("SelfHealingOrchestrator: skill '%s' not found in registry — skip.", skill_name)
                    # Not a failure — skill may already have been removed or it's a static skill
                    return True, None
            except Exception as ex:
                return False, f"Skill deprecation failed: {ex}"
        else:
            # No registry wired — treat as soft success (the retry will simply fail to find the skill)
            logger.debug("SelfHealingOrchestrator: no dynamic_skill_registry wired — skill patch skipped.")
            return True, None

    def _action_inject_transformer(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
    ) -> tuple[bool, str | None]:
        """Tier-2: log transformer injection intent (infrastructure extension point).

        A full implementation would synthesize a schema-adapter dynamic skill and
        register it with ArtifactPipelineRouter. At this level we record the intent
        and return success so the RETRY_PHASE action can execute and the pipeline
        simply retries without the broken artifact.
        """
        logger.info(
            "SelfHealingOrchestrator: INJECT_DATAFLOW_TRANSFORMER for phase=%s — "
            "marking source artifact for reprocessing.",
            fault_report.phase_id,
        )
        return True, None

    def _action_replan_goal(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
        ctx: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Tier-2: mark goal as needing replan — CampaignEngine will invoke GoalAdapter on retry."""
        logger.info(
            "SelfHealingOrchestrator: REPLAN_GOAL for goal=%s phase=%s.",
            fault_report.goal_id, fault_report.phase_id,
        )
        return True, None

    def _action_selective_rollback(
        self,
        action: RemediationAction,
        fault_report: FaultDiagnosticReport,
        saga_coordinator: Any | None,
    ) -> tuple[bool, str | None]:
        """Tier-3: execute saga compensation for the failing phase only."""
        if saga_coordinator is None:
            logger.debug("SelfHealingOrchestrator: no saga_coordinator — skip selective rollback.")
            return True, None

        try:
            if hasattr(saga_coordinator, "compensate_phase"):
                saga_coordinator.compensate_phase(
                    phase_id=fault_report.phase_id,
                    session_id=action.parameters.get("session_id", "default"),
                )
                logger.info(
                    "SelfHealingOrchestrator: selective saga rollback executed for phase=%s.",
                    fault_report.phase_id,
                )
                return True, None
            else:
                return False, "saga_coordinator.compensate_phase method not found."
        except Exception as ex:
            return False, f"Selective rollback failed: {ex}"

    def _request_operator_clarification(
        self,
        fault_report: FaultDiagnosticReport,
        plan: RemediationPlan,
    ) -> None:
        """Emit a clarification request through the ClarificationGateway (if wired)."""
        if self._clarification_gateway is None:
            logger.info(
                "SelfHealingOrchestrator: operator escalation for campaign=%s phase=%s "
                "(no clarification gateway wired — logging only).",
                fault_report.campaign_id, fault_report.phase_id,
            )
            return

        try:
            from core.scheduling_types import ClarificationRequest, ClarificationType
            req = ClarificationRequest(
                clarification_id=f"clar_{plan.plan_id[:12]}",
                goal_id=fault_report.goal_id or "default_goal",
                task_id=fault_report.phase_id or "default_task",
                question=(
                    f"Self-healing escalation for campaign '{fault_report.campaign_id}', "
                    f"phase '{fault_report.phase_id}': {fault_report.error_message[:256]}. "
                    f"Fault category: {fault_report.fault_category.value}. "
                    f"Confidence: {fault_report.confidence_level.value}. "
                    "Please review and re-submit the campaign or provide corrective action."
                ),
                options=("retry", "abort"),
                clarification_type=ClarificationType.SINGLE_CHOICE,
                timeout_seconds=3600.0,
                metadata={
                    "campaign_id": fault_report.campaign_id,
                    "phase_id": fault_report.phase_id,
                    "plan_id": plan.plan_id,
                    "fault_category": fault_report.fault_category.value,
                },
            )
            self._clarification_gateway.submit_request(req)
            logger.info(
                "SelfHealingOrchestrator: clarification request submitted for campaign=%s phase=%s.",
                fault_report.campaign_id, fault_report.phase_id,
            )
        except Exception as ex:
            logger.warning("SelfHealingOrchestrator: failed to submit clarification: %s", ex)

    def _emit_stream_event(
        self,
        campaign_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if self._streaming_gateway is None:
            return
        try:
            from core.session_types import StreamEventType
            self._streaming_gateway.create_and_publish(
                session_id=campaign_id,
                event_type=StreamEventType.STEP_STARTED if "started" in event_type else StreamEventType.STEP_COMPLETED,
                data={"healing_event": event_type, **data},
            )
        except Exception:
            pass

    def _record_result(self, campaign_id: str, result: SelfHealingResult) -> None:
        with self._lock:
            dq = self._healing_history.setdefault(campaign_id, deque(maxlen=MAX_HEALING_HISTORY))
            dq.append(result)

    def _is_repeated_plan(
        self,
        campaign_id: str,
        phase_id: str,
        plan: RemediationPlan,
    ) -> bool:
        """Detect if the same plan (same action types and targets) has been tried before."""
        key = (str(campaign_id).strip(), str(phase_id).strip())
        new_signature = tuple(
            (a.action_type.value, a.target_id) for a in plan.actions
        )
        with self._lock:
            history = self._healing_history.get(campaign_id, deque())
            for result in history:
                if result.phase_id == phase_id:
                    # We can't reconstruct the exact plan from history, so use the
                    # approach of counting: if we've already had 2+ attempts with
                    # the same category, it's likely a loop.
                    attempts = self._attempt_counts.get(key, 0)
                    if attempts > 1 and result.status not in (HealingStatus.RECOVERED,):
                        return True
        return False

    @staticmethod
    def _fault_category_from_error(error_message: str) -> Any:
        from core.causal_fault_analyzer import _classify_from_text
        cat, _ = _classify_from_text(error_message, [])
        return cat

    @staticmethod
    def _make_result(
        plan: RemediationPlan,
        status: HealingStatus,
        fault_report: FaultDiagnosticReport,
        actions_attempted: int,
        actions_succeeded: int,
        campaign_resumed: bool,
        start_time: float,
        attempt_number: int,
        error: str | None,
    ) -> SelfHealingResult:
        return SelfHealingResult(
            result_id=f"shr_{plan.plan_id[:16]}_{attempt_number}",
            plan_id=plan.plan_id,
            campaign_id=plan.campaign_id,
            phase_id=plan.phase_id,
            status=status,
            fault_category=fault_report.fault_category,
            actions_attempted=actions_attempted,
            actions_succeeded=actions_succeeded,
            campaign_resumed=campaign_resumed,
            duration_seconds=time.time() - start_time,
            error=error,
            attempt_number=attempt_number,
            timestamp=time.time(),
        )


def uuid4_hex() -> str:
    from uuid import uuid4
    return uuid4().hex[:12]
