"""Master Autonomous Mission Campaign Engine (M25).

Orchestrates multi-phase mission DAGs, cross-goal artifact dataflow routing,
milestone evaluation gates, team allocation, distributed sagas, and rollback.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from core.artifact_manager import ArtifactManager
from core.artifact_pipeline import ArtifactPipelineRouter
from core.artifact_types import ArtifactType
from core.campaign_types import (
    CampaignDefinition,
    CampaignExecutionResult,
    CampaignMilestone,
    CampaignPhase,
    CampaignStatus,
    CompensatingAction,
    CompensatingActionType,
    DataflowBinding,
    PhaseStatus,
)
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_engine import GoalEngine
from core.goal_scheduler import MultiGoalScheduler
from core.mission_graph import MissionGraph
from core.saga_coordinator import CompensatingActionEngine, SagaCoordinator
from core.session_types import StreamEventType
from core.streaming_gateway import StreamingGateway
from core.team_orchestrator import TeamOrchestrator
from core.trace_types import SpanKind, SpanStatus
from core.tracing import Tracer
from evaluation.engine import EvaluationEngine

logger = logging.getLogger("aura.campaign_engine")


class CampaignEngine:
    """Master coordinator executing multi-goal mission campaigns across teams and pipelines."""

    def __init__(
        self,
        goal_engine: GoalEngine | None = None,
        scheduler: MultiGoalScheduler | None = None,
        team_orchestrator: TeamOrchestrator | None = None,
        artifact_manager: ArtifactManager | None = None,
        evaluation_engine: EvaluationEngine | None = None,
        tracer: Tracer | None = None,
        streaming_gateway: StreamingGateway | None = None,
        compensating_engine: CompensatingActionEngine | None = None,
    ):
        self._lock = threading.RLock()
        self.goal_engine = goal_engine if goal_engine is not None else GoalEngine()
        self.scheduler = scheduler if scheduler is not None else MultiGoalScheduler()
        self.team_orchestrator = team_orchestrator
        self.artifact_manager = artifact_manager if artifact_manager is not None else ArtifactManager()
        self.evaluation_engine = evaluation_engine if evaluation_engine is not None else EvaluationEngine()
        self.tracer = tracer
        self.streaming_gateway = streaming_gateway

        # Active state registries: campaign_id -> component
        self._definitions: dict[str, CampaignDefinition] = {}
        self._graphs: dict[str, MissionGraph] = {}
        self._routers: dict[str, ArtifactPipelineRouter] = {}
        self._sagas: dict[str, SagaCoordinator] = {}
        self._statuses: dict[str, CampaignStatus] = {}

        # Default compensating engine
        self._default_compensating_engine = (
            compensating_engine
            if compensating_engine is not None
            else CompensatingActionEngine(
                artifact_manager=self.artifact_manager,
                lock_manager=getattr(self.scheduler, "lock_manager", None),
                goal_engine=self.goal_engine,
            )
        )

    def submit_campaign(self, definition: CampaignDefinition) -> CampaignDefinition:
        """Register and validate a new mission campaign."""
        if not isinstance(definition, CampaignDefinition):
            raise TypeError("definition must be a CampaignDefinition instance.")

        with self._lock:
            cid = definition.campaign_id
            if cid in self._definitions:
                raise ValueError(f"Campaign with ID '{cid}' is already registered.")

            # Construct and validate mission graph
            graph = MissionGraph(definition.phases)
            graph.validate_graph()

            # Construct artifact pipeline router
            router = ArtifactPipelineRouter(definition.dataflows)

            # Initialize saga coordinator
            saga = SagaCoordinator(engine=self._default_compensating_engine)

            self._definitions[cid] = definition
            self._graphs[cid] = graph
            self._routers[cid] = router
            self._sagas[cid] = saga
            self._statuses[cid] = CampaignStatus.SCHEDULED

            logger.info("Registered and scheduled campaign '%s' with %d phases", cid, len(definition.phases))
            return definition

    def get_campaign(self, campaign_id: str) -> CampaignDefinition | None:
        """Retrieve campaign definition by ID."""
        with self._lock:
            return self._definitions.get(str(campaign_id).strip())

    def get_campaign_graph(self, campaign_id: str) -> MissionGraph | None:
        """Retrieve campaign mission graph by ID."""
        with self._lock:
            return self._graphs.get(str(campaign_id).strip())

    def get_campaign_status(self, campaign_id: str) -> dict[str, Any]:
        """Query real-time status and phase progress of a campaign."""
        clean_cid = str(campaign_id).strip()
        with self._lock:
            defn = self._definitions.get(clean_cid)
            if defn is None:
                return {"status": "not_found", "campaign_id": clean_cid}

            status = self._statuses.get(clean_cid, CampaignStatus.DRAFT)
            graph = self._graphs.get(clean_cid)
            router = self._routers.get(clean_cid)
            saga = self._sagas.get(clean_cid)

            return {
                "campaign_id": clean_cid,
                "title": defn.title,
                "status": status.value,
                "phases": graph.get_phase_summary() if graph else {},
                "bindings_count": len(router.list_bindings()) if router else 0,
                "saga_steps_count": len(saga.log.get_steps()) if saga else 0,
                "session_id": defn.session_id,
                "created_at": defn.created_at,
            }

    def list_campaigns(self) -> list[dict[str, Any]]:
        """List all registered campaigns."""
        with self._lock:
            return [self.get_campaign_status(cid) for cid in self._definitions]

    def execute_campaign(
        self,
        campaign_id: str,
        session_id: str | None = None,
        max_iterations: int = 50,
    ) -> CampaignExecutionResult:
        """Execute all phases in a campaign DAG to completion, handling dataflows and sagas."""
        clean_cid = str(campaign_id).strip()
        start_time = time.time()

        with self._lock:
            defn = self._definitions.get(clean_cid)
            if defn is None:
                raise KeyError(f"Campaign '{clean_cid}' is not registered.")
            graph = self._graphs[clean_cid]
            router = self._routers[clean_cid]
            saga = self._sagas[clean_cid]

        eff_session_id = session_id or defn.session_id or "default"

        # Distributed tracing
        campaign_span = None
        if self.tracer is not None:
            campaign_span = self.tracer.start_span(
                name=f"campaign_{defn.title}",
                kind=SpanKind.INTERNAL,
                attributes={"campaign_id": clean_cid, "session_id": eff_session_id},
            )

        with self._lock:
            self._statuses[clean_cid] = CampaignStatus.RUNNING

        completed_phases: list[str] = []
        failed_phases: list[str] = []
        compensated_phases: list[str] = []
        produced_artifacts: list[str] = []
        milestone_scores: dict[str, float] = {}
        execution_error: str | None = None

        iterations = 0
        try:
            while iterations < max_iterations:
                iterations += 1

                # Check total time limit
                if (time.time() - start_time) > defn.max_total_time_seconds:
                    raise TimeoutError(
                        f"Campaign '{clean_cid}' exceeded max execution time ({defn.max_total_time_seconds}s)."
                    )

                # Discover ready phases
                ready_phases = graph.get_ready_phases()

                if not ready_phases:
                    if graph.is_terminal():
                        break
                    # No ready phases but not terminal -> stagnation/blocked
                    if graph.has_failed_phases():
                        break
                    # Wait briefly if concurrent workers are running
                    time.sleep(0.05)
                    continue

                for phase in ready_phases:
                    pid = phase.phase_id
                    graph.update_phase_status(pid, PhaseStatus.RUNNING, started_at=time.time())

                    # Stream phase start
                    if self.streaming_gateway is not None:
                        self.streaming_gateway.create_and_publish(
                            session_id=eff_session_id,
                            event_type=StreamEventType.STEP_STARTED,
                            data={"campaign_id": clean_cid, "phase_id": pid, "phase_name": phase.name},
                        )

                    phase_success = True
                    phase_error = None

                    # Execute goals in this phase
                    for gid in phase.goal_ids:
                        # 1. Retrieve & bind input artifacts from upstream channels
                        input_artifacts = router.get_input_artifacts_for_goal(
                            consumer_goal_id=gid,
                            artifact_manager=self.artifact_manager,
                        )

                        # 2. Ensure goal exists in GoalEngine
                        if self.goal_engine.goal_store.exists(gid):
                            goal_obj = self.goal_engine.get_goal(gid)
                        else:
                            # Create goal with dataflow input context
                            goal_obj = Goal(
                                goal_id=gid,
                                title=f"Goal for Phase {phase.name}",
                                metadata={"pipeline_inputs": input_artifacts},
                            )
                            self.goal_engine.goal_store.create(goal_obj)

                        # 3. Execute via TeamOrchestrator if assigned, else evaluate via GoalEngine
                        goal_output_artifacts: list[str] = []
                        if phase.assigned_team_id and self.team_orchestrator is not None:
                            team_res = self.team_orchestrator.execute_team(
                                task=goal_obj.title,
                                session_id=eff_session_id,
                                metadata={"goal_id": gid, "phase_id": pid, "inputs": input_artifacts},
                            )
                            if not team_res.success:
                                phase_success = False
                                phase_error = team_res.error or "Team execution failed"
                                break
                            # If team created an artifact, capture it
                            if self.artifact_manager is not None:
                                art = self.artifact_manager.store_artifact(
                                    name=f"{phase.name}_output.json",
                                    content=team_res.final_output,
                                    artifact_type=ArtifactType.DOCUMENT,
                                    session_id=eff_session_id,
                                    creator_role_id=phase.assigned_role_id or "team_worker",
                                    producer_goal_id=gid,
                                    producer_task_id=pid,
                                )
                                goal_output_artifacts.append(art.artifact_id)
                                produced_artifacts.append(art.artifact_id)
                        else:
                            # Direct evaluation
                            try:
                                eval_res = self.goal_engine.evaluate_goal(gid)
                                if hasattr(eval_res, "status") and eval_res.status == GoalStatus.FAILED:
                                    phase_success = False
                                    phase_error = f"Goal '{gid}' evaluation failed."
                                    break
                                # Auto-generate report artifact if missing
                                art = self.artifact_manager.store_artifact(
                                    name=f"{phase.name}_result.json",
                                    content={"status": "completed", "goal_id": gid},
                                    artifact_type=ArtifactType.REPORT,
                                    session_id=eff_session_id,
                                    creator_role_id=phase.assigned_role_id or "system",
                                    producer_goal_id=gid,
                                    producer_task_id=pid,
                                )
                                goal_output_artifacts.append(art.artifact_id)
                                produced_artifacts.append(art.artifact_id)
                            except Exception as ex:
                                phase_success = False
                                phase_error = str(ex)
                                break

                        # 4. Route goal output artifacts to downstream channels
                        router.route_goal_artifacts(
                            producer_goal_id=gid,
                            artifact_ids=goal_output_artifacts,
                            artifact_manager=self.artifact_manager,
                        )

                        # 5. Record forward saga step & register compensating actions
                        compensating_acts: list[CompensatingAction] = []
                        for out_aid in goal_output_artifacts:
                            compensating_acts.append(
                                CompensatingAction(
                                    action_id=f"comp_{uuid4().hex[:8]}",
                                    action_type=CompensatingActionType.TOMBSTONE_ARTIFACT,
                                    target_id=out_aid,
                                    parameters={"reason": f"Rollback phase {pid}"},
                                )
                            )
                        compensating_acts.append(
                            CompensatingAction(
                                action_id=f"comp_locks_{uuid4().hex[:8]}",
                                action_type=CompensatingActionType.RELEASE_LOCKS,
                                target_id=gid,
                            )
                        )

                        saga.record_forward_step(
                            goal_id=gid,
                            phase_id=pid,
                            forward_result={"status": "success", "artifacts": goal_output_artifacts},
                            compensating_actions=compensating_acts,
                        )

                    # 6. Milestone Evaluation Gate
                    if phase_success and phase.milestones:
                        verified_milestones = []
                        for ms in phase.milestones:
                            # Evaluate milestone criteria using EvaluationEngine
                            eval_report = self.evaluation_engine.evaluate(
                                target={"phase_id": pid, "milestone_id": ms.milestone_id},
                                target_id=ms.milestone_id,
                                target_type="milestone",
                                expected_criteria=ms.criteria,
                            )
                            score = getattr(eval_report, "overall_score", 1.0)
                            milestone_scores[ms.milestone_id] = score

                            is_pass = score >= ms.min_evaluation_score
                            verified_ms = ms.with_verification(is_verified=is_pass, score=score)
                            verified_milestones.append(verified_ms)

                            if not is_pass:
                                phase_success = False
                                phase_error = (
                                    f"Milestone '{ms.title}' failed gate with score {score:.2f} "
                                    f"(minimum {ms.min_evaluation_score:.2f})."
                                )
                                break

                        # Update phase with verified milestones
                        graph.update_phase(phase.with_status(
                            status=phase.status,
                            milestones=tuple(verified_milestones),
                        ))

                    # 7. Finalize Phase outcome
                    if phase_success:
                        graph.update_phase_status(pid, PhaseStatus.COMPLETED, completed_at=time.time())
                        completed_phases.append(pid)
                    else:
                        graph.update_phase_status(pid, PhaseStatus.FAILED, completed_at=time.time(), error=phase_error)
                        failed_phases.append(pid)
                        execution_error = phase_error

                        # Check auto-compensation
                        if defn.auto_compensate_on_failure:
                            logger.warning("Phase '%s' failed; initiating saga compensation", pid)
                            saga.compensate_phase(pid, session_id=eff_session_id)
                            compensated_phases.append(pid)
                        break  # Stop processing further ready phases on failure

            # Final Campaign Status Determination
            with self._lock:
                if graph.is_successful():
                    final_status = CampaignStatus.COMPLETED
                elif graph.has_failed_phases() or failed_phases:
                    final_status = CampaignStatus.FAILED
                else:
                    final_status = CampaignStatus.CANCELLED
                self._statuses[clean_cid] = final_status

        except Exception as e:
            logger.error("Exception executing campaign '%s': %s", clean_cid, e)
            execution_error = str(e)
            with self._lock:
                self._statuses[clean_cid] = CampaignStatus.FAILED
                final_status = CampaignStatus.FAILED
            if defn.auto_compensate_on_failure:
                saga.compensate_all(session_id=eff_session_id)
                compensated_phases.extend(failed_phases)

        finally:
            if campaign_span is not None:
                campaign_span.set_status(
                    SpanStatus.OK if final_status == CampaignStatus.COMPLETED else SpanStatus.ERROR,
                    message=execution_error or "",
                )
                try:
                    campaign_span.end()
                except Exception:
                    pass

        duration = time.time() - start_time
        return CampaignExecutionResult(
            campaign_id=clean_cid,
            status=final_status,
            completed_phases=tuple(completed_phases),
            failed_phases=tuple(failed_phases),
            compensated_phases=tuple(compensated_phases),
            produced_artifact_ids=tuple(produced_artifacts),
            milestone_scores=milestone_scores,
            total_duration_seconds=duration,
            error=execution_error,
        )

    def cancel_campaign(self, campaign_id: str, reason: str = "User cancelled") -> bool:
        """Cancel a running or scheduled campaign and trigger compensations if needed."""
        clean_cid = str(campaign_id).strip()
        with self._lock:
            defn = self._definitions.get(clean_cid)
            if defn is None:
                return False
            curr = self._statuses.get(clean_cid)
            if curr and curr.is_terminal():
                return False
            self._statuses[clean_cid] = CampaignStatus.CANCELLED
            saga = self._sagas.get(clean_cid)
            if saga and defn.auto_compensate_on_failure:
                saga.compensate_all(session_id=defn.session_id or "default")
            logger.info("Campaign '%s' cancelled: %s", clean_cid, reason)
            return True

    def rollback_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        """Manually trigger a complete rollback of all executed steps in a campaign."""
        clean_cid = str(campaign_id).strip()
        with self._lock:
            defn = self._definitions.get(clean_cid)
            if defn is None:
                raise KeyError(f"Campaign '{clean_cid}' is not registered.")
            saga = self._sagas.get(clean_cid)
            if saga is None:
                return []
            self._statuses[clean_cid] = CampaignStatus.COMPENSATING
            results = saga.compensate_all(session_id=defn.session_id or "default")
            self._statuses[clean_cid] = CampaignStatus.FAILED
            return results
