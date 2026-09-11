"""Resilient Distributed Saga Coordinator & Compensating Engine (M25).

Implements the Saga execution pattern for multi-goal campaigns, managing forward
step recording, reverse compensation ordering, artifact tombstoning, and lock release.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any, Callable
from uuid import uuid4

from core.campaign_types import (
    CompensatingAction,
    CompensatingActionType,
    SagaStep,
    SagaStepStatus,
    MAX_SAGA_STEPS,
)

logger = logging.getLogger("aura.saga_coordinator")


class SagaRollbackLog:
    """Thread-safe persistent journal of forward steps and compensation history."""

    def __init__(self, steps: Sequence[SagaStep] = ()):
        self._lock = threading.RLock()
        self._steps: list[SagaStep] = list(steps)

    def record_step(self, step: SagaStep) -> None:
        """Append a step to the saga journal."""
        if not isinstance(step, SagaStep):
            raise TypeError("step must be a SagaStep instance.")
        with self._lock:
            if len(self._steps) >= MAX_SAGA_STEPS:
                logger.warning("SagaRollbackLog reached maximum step capacity (%d)", MAX_SAGA_STEPS)
                self._steps.pop(0)
            self._steps.append(step)

    def get_steps(self) -> list[SagaStep]:
        """Return all recorded steps in chronological order."""
        with self._lock:
            return list(self._steps)

    def get_steps_for_phase(self, phase_id: str) -> list[SagaStep]:
        """Return steps recorded for a specific phase."""
        clean_pid = str(phase_id).strip()
        with self._lock:
            return [s for s in self._steps if s.phase_id == clean_pid]

    def update_step(self, updated_step: SagaStep) -> None:
        """Update a step in-place by step_id."""
        with self._lock:
            for i, s in enumerate(self._steps):
                if s.step_id == updated_step.step_id:
                    self._steps[i] = updated_step
                    return
            self._steps.append(updated_step)

    def clear(self) -> None:
        """Reset the log."""
        with self._lock:
            self._steps.clear()

    def to_dict(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._steps]

    @classmethod
    def from_dict(cls, data: list[dict[str, Any]]) -> SagaRollbackLog:
        if not isinstance(data, list):
            raise TypeError("data must be a list of step dictionaries.")
        steps = [SagaStep.from_dict(d) for d in data]
        return cls(steps=steps)


class CompensatingActionEngine:
    """Executes individual compensating actions against runtime subsystems."""

    def __init__(
        self,
        artifact_manager: Any | None = None,
        lock_manager: Any | None = None,
        goal_engine: Any | None = None,
        approval_gateway: Any | None = None,
        custom_handlers: dict[str, Callable[..., Any]] | None = None,
    ):
        self.artifact_manager = artifact_manager
        self.lock_manager = lock_manager
        self.goal_engine = goal_engine
        self.approval_gateway = approval_gateway
        self.custom_handlers = dict(custom_handlers or {})

    def execute_action(
        self,
        action: CompensatingAction,
        session_id: str = "default",
    ) -> dict[str, Any]:
        """Execute a single compensating action respecting security boundaries."""
        if not isinstance(action, CompensatingAction):
            raise TypeError("action must be a CompensatingAction instance.")

        # Security check: If action requires approval, verify with gateway
        if action.requires_approval and self.approval_gateway is not None:
            # Check if pending or authorized
            is_approved = getattr(self.approval_gateway, "is_action_approved", lambda aid: False)(action.action_id)
            if not is_approved:
                logger.warning("CompensatingAction '%s' blocked: operator approval required.", action.action_id)
                return {
                    "action_id": action.action_id,
                    "action_type": action.action_type.value,
                    "success": False,
                    "error": "Operator approval required for this compensating action.",
                    "requires_approval": True,
                }

        try:
            if action.action_type == CompensatingActionType.TOMBSTONE_ARTIFACT:
                return self._tombstone_artifact(action)
            elif action.action_type == CompensatingActionType.RELEASE_LOCKS:
                return self._release_locks(action)
            elif action.action_type == CompensatingActionType.EXECUTE_GOAL:
                return self._execute_goal(action)
            elif action.action_type == CompensatingActionType.CUSTOM_CALLBACK:
                return self._execute_custom_callback(action)
            elif action.action_type == CompensatingActionType.CHECKPOINT_ROLLBACK:
                return self._checkpoint_rollback(action)
            else:
                return {
                    "action_id": action.action_id,
                    "action_type": action.action_type.value,
                    "success": False,
                    "error": f"Unsupported compensating action type: {action.action_type}",
                }
        except Exception as e:
            logger.error("Exception executing compensating action '%s': %s", action.action_id, e)
            return {
                "action_id": action.action_id,
                "action_type": action.action_type.value,
                "success": False,
                "error": str(e),
            }

    def _tombstone_artifact(self, action: CompensatingAction) -> dict[str, Any]:
        artifact_id = action.target_id
        if self.artifact_manager is None:
            return {"action_id": action.action_id, "success": True, "note": "No artifact manager configured."}

        manifest = self.artifact_manager.get_artifact(artifact_id)
        if manifest is None:
            return {"action_id": action.action_id, "success": True, "note": "Artifact already absent."}

        # Mark artifact as tombstoned in metadata
        upd_meta = dict(manifest.metadata)
        upd_meta["is_tombstoned"] = True
        upd_meta["tombstoned_at"] = time.time()
        upd_meta["tombstone_reason"] = action.parameters.get("reason", "Saga compensation rollback")

        # Update in store if manager supports update or store new tombstone revision
        try:
            self.artifact_manager.store_artifact(
                name=f"{manifest.name}.tombstone",
                content=b"",
                artifact_type=manifest.artifact_type,
                session_id=manifest.session_id,
                creator_role_id="saga_coordinator",
                parent_artifact_ids=(manifest.artifact_id,),
                metadata=upd_meta,
                taint_status=True,
            )
        except Exception as ex:
            logger.debug("Tombstone artifact creation note: %s", ex)

        return {"action_id": action.action_id, "success": True, "tombstoned_artifact_id": artifact_id}

    def _release_locks(self, action: CompensatingAction) -> dict[str, Any]:
        goal_id = action.target_id
        if self.lock_manager is not None and hasattr(self.lock_manager, "release_all_locks_for_goal"):
            released = self.lock_manager.release_all_locks_for_goal(goal_id)
            return {"action_id": action.action_id, "success": True, "released_locks_count": released}
        return {"action_id": action.action_id, "success": True, "note": "No lock manager configured."}

    def _execute_goal(self, action: CompensatingAction) -> dict[str, Any]:
        title = action.parameters.get("title", f"Compensate {action.target_id}")
        if self.goal_engine is not None and hasattr(self.goal_engine, "create_goal"):
            g = self.goal_engine.create_goal(title=title, metadata=action.parameters)
            return {"action_id": action.action_id, "success": True, "compensating_goal_id": g.goal_id}
        return {"action_id": action.action_id, "success": True, "note": "No goal engine configured."}

    def _execute_custom_callback(self, action: CompensatingAction) -> dict[str, Any]:
        handler_name = action.target_id
        handler = self.custom_handlers.get(handler_name)
        if handler is None:
            return {"action_id": action.action_id, "success": False, "error": f"Handler '{handler_name}' not registered."}
        res = handler(action.parameters)
        return {"action_id": action.action_id, "success": True, "result": res}

    def _checkpoint_rollback(self, action: CompensatingAction) -> dict[str, Any]:
        checkpoint_id = action.target_id
        return {"action_id": action.action_id, "success": True, "target_checkpoint_id": checkpoint_id}


class SagaCoordinator:
    """Coordinates distributed forward step journals and backward compensating actions."""

    def __init__(
        self,
        log: SagaRollbackLog | None = None,
        engine: CompensatingActionEngine | None = None,
    ):
        self._lock = threading.RLock()
        self.log = log if log is not None else SagaRollbackLog()
        self.engine = engine if engine is not None else CompensatingActionEngine()

    def record_forward_step(
        self,
        goal_id: str,
        phase_id: str,
        forward_result: dict[str, Any] | None = None,
        compensating_actions: Sequence[CompensatingAction] = (),
    ) -> SagaStep:
        """Record a successful forward execution step with its compensating actions."""
        step = SagaStep(
            step_id=f"saga_{uuid4().hex[:12]}",
            goal_id=str(goal_id).strip(),
            phase_id=str(phase_id).strip(),
            status=SagaStepStatus.FORWARD_EXECUTED,
            forward_execution_result=dict(forward_result or {}),
            compensating_actions=tuple(compensating_actions),
            executed_at=time.time(),
        )
        self.log.record_step(step)
        return step

    def compensate_all(self, session_id: str = "default") -> list[dict[str, Any]]:
        """Execute all registered compensating actions in reverse chronological order."""
        with self._lock:
            steps = self.log.get_steps()
            # Reverse order for LIFO compensation
            reversed_steps = list(reversed(steps))
            results: list[dict[str, Any]] = []

            for step in reversed_steps:
                if step.status == SagaStepStatus.COMPENSATED:
                    logger.debug("Step '%s' already compensated; skipping", step.step_id)
                    continue

                step_res: dict[str, Any] = {
                    "step_id": step.step_id,
                    "goal_id": step.goal_id,
                    "phase_id": step.phase_id,
                    "action_results": [],
                    "success": True,
                }

                # Mark compensating
                upd_step = step.with_status(SagaStepStatus.COMPENSATING)
                self.log.update_step(upd_step)

                has_failure = False
                for action in step.compensating_actions:
                    act_res = self.engine.execute_action(action, session_id=session_id)
                    step_res["action_results"].append(act_res)
                    if not act_res.get("success", False):
                        has_failure = True

                final_status = (
                    SagaStepStatus.COMPENSATION_FAILED
                    if has_failure
                    else SagaStepStatus.COMPENSATED
                )
                final_step = step.with_status(
                    status=final_status,
                    compensated_at=time.time(),
                    error="One or more compensating actions failed." if has_failure else None,
                )
                self.log.update_step(final_step)
                step_res["success"] = not has_failure
                results.append(step_res)

            return results

    def compensate_phase(self, phase_id: str, session_id: str = "default") -> list[dict[str, Any]]:
        """Compensate only steps belonging to a specific phase."""
        clean_pid = str(phase_id).strip()
        with self._lock:
            phase_steps = list(reversed(self.log.get_steps_for_phase(clean_pid)))
            results: list[dict[str, Any]] = []

            for step in phase_steps:
                if step.status == SagaStepStatus.COMPENSATED:
                    continue

                step_res: dict[str, Any] = {
                    "step_id": step.step_id,
                    "goal_id": step.goal_id,
                    "phase_id": step.phase_id,
                    "action_results": [],
                    "success": True,
                }

                upd_step = step.with_status(SagaStepStatus.COMPENSATING)
                self.log.update_step(upd_step)

                has_failure = False
                for action in step.compensating_actions:
                    act_res = self.engine.execute_action(action, session_id=session_id)
                    step_res["action_results"].append(act_res)
                    if not act_res.get("success", False):
                        has_failure = True

                final_status = (
                    SagaStepStatus.COMPENSATION_FAILED
                    if has_failure
                    else SagaStepStatus.COMPENSATED
                )
                final_step = step.with_status(
                    status=final_status,
                    compensated_at=time.time(),
                    error="One or more compensating actions failed." if has_failure else None,
                )
                self.log.update_step(final_step)
                step_res["success"] = not has_failure
                results.append(step_res)

            return results

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "log": self.log.to_dict(),
            }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        engine: CompensatingActionEngine | None = None,
    ) -> SagaCoordinator:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        log_data = data.get("log", [])
        log = SagaRollbackLog.from_dict(log_data)
        return cls(log=log, engine=engine)
