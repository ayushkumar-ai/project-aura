"""M59 — Unified Autonomous Agent Runtime.

Coordinates the 16-phase deterministic agent execution loop, state machine transitions,
context assembly, planning, policy/approval gating, dispatch, verification, bounded reflection,
learning loop, and cancellation cascades.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.agent_mesh.context import ContextFabric
from core.agent_mesh.dispatcher import ActionDispatcher
from core.agent_mesh.intent import IntentClassifier
from core.agent_mesh.learning import MemoryLearningBridge
from core.agent_mesh.mesh import IntelligenceMeshCoordinator
from core.agent_mesh.planner import StructuredPlanner
from core.agent_mesh.reflection import BoundedReflectionEngine
from core.agent_mesh.types import (
    AgentMeshAudit,
    AgentMeshEvent,
    AgentPhase,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
    AgentRunStep,
    FailureCategory,
    VerificationStatus,
)
from core.agent_mesh.validator import PlanValidator
from core.agent_mesh.verifier import ResultVerifier
from core.metrics import get_metrics_registry

if TYPE_CHECKING:
    from core.model_gateway import ModelGateway
    from core.platform.gateway import PlatformIntegrationGateway
    from core.platform.security import PlatformSecurityManager
    from core.repositories.base_agent_mesh import BaseAgentMeshRepository
    from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository

logger = logging.getLogger("aura.agent_mesh.runtime")


class UnifiedAgentRuntime:
    """Production runtime unifying agent intelligence across M1–M58."""

    def __init__(
        self,
        repository: BaseAgentMeshRepository | Any,
        model_gateway: ModelGateway | Any | None = None,
        platform_gateway: PlatformIntegrationGateway | None = None,
        memory_repo: BaseCognitiveMemoryRepository | None = None,
        policy_engine: Any | None = None,
        approval_engine: Any | None = None,
        security_manager: PlatformSecurityManager | None = None,
    ):
        self.repository = repository
        self.model_gateway = model_gateway
        self.platform_gateway = platform_gateway
        self.memory_repo = memory_repo
        self.policy_engine = policy_engine
        self.approval_engine = approval_engine
        self.security_manager = security_manager

        # Subsystem Components
        self.intent_classifier = IntentClassifier()
        self.context_fabric = ContextFabric(memory_repo=memory_repo, platform_repo=platform_gateway.repository if platform_gateway else None)
        self.planner = StructuredPlanner(model_gateway=model_gateway)
        self.validator = PlanValidator(policy_engine=policy_engine, approval_engine=approval_engine, security_manager=security_manager)
        self.mesh_coordinator = IntelligenceMeshCoordinator(repository=repository, runtime=self)
        self.dispatcher = ActionDispatcher(platform_gateway=platform_gateway, memory_repo=memory_repo, mesh_coordinator=self.mesh_coordinator)
        self.verifier = ResultVerifier()
        self.reflector = BoundedReflectionEngine()
        self.learning_bridge = MemoryLearningBridge(memory_repo=memory_repo)

    def create_run(
        self,
        tenant_id: str,
        user_id: str,
        intent: str,
        budget: AgentRunBudget | None = None,
        parent_run_id: str | None = None,
    ) -> AgentRun:
        """Create and persist a new AgentRun domain record (Invariant M59-F01)."""
        run = AgentRun(
            run_id=f"run_{uuid4().hex[:16]}",
            tenant_id=tenant_id,
            user_id=user_id,
            parent_run_id=parent_run_id,
            intent=intent,
            status=AgentRunStatus.PENDING,
            current_phase=AgentPhase.RECEIVE,
            budget=budget or AgentRunBudget(),
            created_at=time.time(),
        )
        saved = self.repository.save_run(run)
        self._emit_event(saved, "run_created", AgentPhase.RECEIVE, {"intent": intent})
        return saved

    def execute_run(
        self,
        run: AgentRun,
        approval_token: str | None = None,
    ) -> AgentRun:
        """Execute the Unified Agent Loop across all 16 phases (Invariants M59-F01 through M59-F50)."""
        start_time = time.time()

        if run.is_terminal():
            return run

        if run.status == AgentRunStatus.PAUSED:
            return run

        # Transition to RUNNING
        if run.status in (AgentRunStatus.PENDING, AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_EXTERNAL):
            run.transition_to(AgentRunStatus.RUNNING)
            self.repository.save_run(run)

        try:
            # Phase 1: Intent Classification
            run.current_phase = AgentPhase.INTENT
            classified = self.intent_classifier.classify(run.intent)
            self._emit_event(run, "intent_classified", AgentPhase.INTENT, classified.to_dict())

            # Phase 2: Context Assembly (Cognitive Context Fabric)
            run.current_phase = AgentPhase.CONTEXT
            context = self.context_fabric.assemble_context(
                tenant_id=run.tenant_id,
                user_id=run.user_id,
                query=classified.cleaned_goal,
            )
            self._emit_event(run, "context_assembled", AgentPhase.CONTEXT, {"memories_count": len(context.memories)})

            # Phase 3: Structured Planning (via ModelGateway)
            run.current_phase = AgentPhase.PLAN
            plan = self.planner.plan(
                tenant_id=run.tenant_id,
                intent=classified,
                context=context,
                max_steps=5,
            )
            run.provider_call_count += 1
            self._emit_event(run, "plan_generated", AgentPhase.PLAN, plan.to_dict())

            # Phase 4: Plan Validation & Policy Gating
            run.current_phase = AgentPhase.VALIDATE_PLAN
            self.validator.validate_plan(tenant_id=run.tenant_id, plan=plan)

            # Phase 5: Step Execution Loop
            for step in plan.steps:
                # Check wall-clock timeout budget (Invariant M59-F46)
                if (time.time() - start_time) > run.budget.timeout_seconds:
                    run.transition_to(AgentRunStatus.TIMED_OUT, error_detail="Execution exceeded timeout budget.")
                    self.repository.save_run(run)
                    return run

                # Check iteration budget (Invariant M59-F43)
                run.iteration_count += 1
                if run.iteration_count > run.budget.max_iterations:
                    run.transition_to(AgentRunStatus.TIMED_OUT, error_detail="Iteration limit exceeded.")
                    self.repository.save_run(run)
                    return run

                # Phase: Approval Gate
                if step.requires_approval:
                    if not approval_token:
                        # Transition to WAITING_APPROVAL
                        run.transition_to(AgentRunStatus.WAITING_APPROVAL)
                        run.current_phase = AgentPhase.APPROVAL_GATE
                        self.repository.save_run(run)
                        self._emit_event(run, "approval_required", AgentPhase.APPROVAL_GATE, {"step": step.to_dict()})
                        return run

                # Pre-execution validation
                self.validator.validate_step_pre_execution(
                    tenant_id=run.tenant_id,
                    step=step,
                    approval_token=approval_token,
                )

                # Phase: Dispatch & Execute
                run.current_phase = AgentPhase.EXECUTE
                run.tool_call_count += 1
                exec_outcome = self.dispatcher.dispatch(
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                    step=step,
                    approval_token=approval_token,
                )

                # Phase: Verification
                run.current_phase = AgentPhase.VERIFY
                v_status, v_detail = self.verifier.verify_step_outcome(step, exec_outcome)

                # Record step in repository
                step_record = AgentRunStep(
                    run_id=run.run_id,
                    tenant_id=run.tenant_id,
                    step_number=step.step_number,
                    phase=AgentPhase.VERIFY,
                    plan_action=step.action_name,
                    action_type=step.action_type,
                    parameters=step.parameters,
                    result=exec_outcome.get("result", {}),
                    status="completed" if v_status == VerificationStatus.VERIFIED_SUCCESS else "failed",
                    verification_status=v_status,
                    approval_token=approval_token,
                    duration_ms=exec_outcome.get("duration_ms", 0.0),
                    completed_at=time.time(),
                )
                self.repository.save_step(step_record)

                # If step failed, reflect and check if retry is permissible
                if v_status == VerificationStatus.VERIFIED_FAILURE:
                    run.current_phase = AgentPhase.REFLECT
                    reflection = self.reflector.evaluate_failure(
                        step=step,
                        verification_status=v_status,
                        verification_detail=v_detail,
                        current_retry_count=0,
                        budget=run.budget,
                    )
                    if reflection.abort_execution:
                        raise RuntimeError(f"Step {step.step_number} failed verification: {v_detail}")

            # Phase 6: Learning Loop (Emit feedback to M56)
            run.current_phase = AgentPhase.LEARN
            self.learning_bridge.record_run_experience(run)

            # Phase 7: Finalize Run
            run.current_phase = AgentPhase.FINALIZE
            run.final_outcome = {"goal": run.intent, "status": "succeeded", "steps_executed": len(plan.steps)}
            run.transition_to(AgentRunStatus.COMPLETED)
            self.repository.save_run(run)

            self._emit_event(run, "run_completed", AgentPhase.FINALIZE, run.final_outcome)
            self._record_metrics(run, time.time() - start_time, success=True)

        except PermissionError as pe:
            logger.warning(f"Security/Policy violation in AgentRun '{run.run_id}': {pe}")
            run.transition_to(AgentRunStatus.FAILED, error_detail=f"Security violation: {pe}")
            self.repository.save_run(run)
            self._emit_event(run, "security_violation", AgentPhase.FINALIZE, {"error": str(pe)})
            self._record_metrics(run, time.time() - start_time, success=False)

        except Exception as e:
            logger.error(f"Error executing AgentRun '{run.run_id}': {e}", exc_info=True)
            run.transition_to(AgentRunStatus.FAILED, error_detail=str(e))
            self.repository.save_run(run)
            self._emit_event(run, "run_failed", AgentPhase.FINALIZE, {"error": str(e)})
            self._record_metrics(run, time.time() - start_time, success=False)

        return run

    def cancel_run(self, run_id: str, tenant_id: str) -> AgentRun:
        """Cancel an active AgentRun and cascade cancellation to all child runs (Invariants M59-F04, M59-F05)."""
        run = self.repository.get_run(run_id, tenant_id)
        if not run:
            raise ValueError(f"AgentRun '{run_id}' not found for tenant '{tenant_id}'.")

        if not run.is_terminal():
            run.transition_to(AgentRunStatus.CANCELLED, error_detail="User requested cancellation.")
            self.repository.save_run(run)
            self._emit_event(run, "run_cancelled", AgentPhase.FINALIZE, {})

        # Cascade cancellation to children
        child_delegations = self.repository.list_delegations(run_id, tenant_id)
        for del_rec in child_delegations:
            try:
                self.cancel_run(del_rec.child_run_id, tenant_id)
            except Exception as e:
                logger.warning(f"Error cascading cancellation to child run '{del_rec.child_run_id}': {e}")

        return run

    def pause_run(self, run_id: str, tenant_id: str) -> AgentRun:
        """Pause an active AgentRun (Invariant M59-F06)."""
        run = self.repository.get_run(run_id, tenant_id)
        if not run:
            raise ValueError(f"AgentRun '{run_id}' not found for tenant '{tenant_id}'.")
        run.transition_to(AgentRunStatus.PAUSED)
        self.repository.save_run(run)
        self._emit_event(run, "run_paused", run.current_phase, {})
        return run

    def resume_run(self, run_id: str, tenant_id: str) -> AgentRun:
        """Resume a paused AgentRun."""
        run = self.repository.get_run(run_id, tenant_id)
        if not run:
            raise ValueError(f"AgentRun '{run_id}' not found for tenant '{tenant_id}'.")
        if run.status != AgentRunStatus.PAUSED:
            raise ValueError(f"Cannot resume run with status '{run.status.value}'.")
        run.transition_to(AgentRunStatus.RUNNING)
        self.repository.save_run(run)
        self._emit_event(run, "run_resumed", run.current_phase, {})
        return self.execute_run(run)

    def approve_step(self, run_id: str, tenant_id: str, approval_token: str) -> AgentRun:
        """Submit human approval token and resume execution for a waiting AgentRun."""
        run = self.repository.get_run(run_id, tenant_id)
        if not run:
            raise ValueError(f"AgentRun '{run_id}' not found for tenant '{tenant_id}'.")
        if run.status != AgentRunStatus.WAITING_APPROVAL:
            raise ValueError(f"AgentRun '{run_id}' is not in WAITING_APPROVAL state (current: {run.status.value}).")
        return self.execute_run(run, approval_token=approval_token)

    def _emit_event(self, run: AgentRun, event_type: str, phase: AgentPhase, data: dict[str, Any]) -> None:
        try:
            evt = AgentMeshEvent(
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                event_type=event_type,
                phase=phase,
                data=data,
            )
            self.repository.save_event(evt)
        except Exception as e:
            logger.warning(f"Failed to record agent mesh event: {e}")

    def _record_metrics(self, run: AgentRun, duration_sec: float, success: bool) -> None:
        try:
            metrics = get_metrics_registry()
            metrics.get_counter("aura_agent_runs_total").inc(
                labels={
                    "status": run.status.value,
                    "depth": str(run.depth),
                }
            )
            metrics.get_histogram("aura_agent_run_duration_seconds").observe(
                duration_sec,
                labels={"status": run.status.value},
            )
        except Exception:
            pass
