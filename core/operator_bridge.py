import logging
import threading
import time
from typing import Any
from uuid import uuid4

from app.config import settings
from core.approval import ApprovalGateway, ApprovalRequest, ApprovalStatus
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import ClarificationRequest, ClarificationStatus
from core.session_types import (
    OperatorAction,
    OperatorActionType,
    OperatorResolution,
    StreamEventType,
    canonical_session_value,
    sanitize_session_metadata,
    validate_session_id,
)
from core.streaming_gateway import StreamingGateway

logger = logging.getLogger("aura.operator_bridge")


class OperatorBridge:
    """Human-in-the-Loop interactive gateway connecting ApprovalGateway, ClarificationGateway, StreamingGateway, and Supervisor."""

    def __init__(
        self,
        approval_gateway: ApprovalGateway | None = None,
        clarification_gateway: ClarificationGateway | None = None,
        streaming_gateway: StreamingGateway | None = None,
        supervisor: Any | None = None,
        default_operator_timeout: float | None = None,
    ):
        self.approval_gateway = approval_gateway if approval_gateway is not None else ApprovalGateway()
        self.clarification_gateway = (
            clarification_gateway if clarification_gateway is not None else ClarificationGateway()
        )
        self.streaming_gateway = streaming_gateway if streaming_gateway is not None else StreamingGateway()
        self.supervisor = supervisor
        self.default_operator_timeout: float = (
            float(default_operator_timeout)
            if default_operator_timeout is not None
            else getattr(settings, "aura_operator_timeout_seconds", 300.0)
        )

        self._lock = threading.RLock()
        self._audit_log: list[dict[str, Any]] = []

    def submit_action(self, action: OperatorAction) -> OperatorResolution:
        """Process an operator action, update underlying gateways, emit streaming events, and record audit log."""
        if not isinstance(action, OperatorAction):
            raise TypeError("action must be an instance of OperatorAction.")

        with self._lock:
            # Audit recording entry
            audit_entry = {
                "action_id": action.action_id,
                "session_id": action.session_id,
                "request_id": action.request_id,
                "action_type": action.action_type.value,
                "operator_id": action.operator_id,
                "timestamp": action.timestamp,
                "decision_rationale": action.decision_rationale,
                "metadata": sanitize_session_metadata(action.metadata),
            }
            self._audit_log.append(audit_entry)

            if action.action_type == OperatorActionType.APPROVE:
                return self._handle_approve(action)
            elif action.action_type == OperatorActionType.REJECT:
                return self._handle_reject(action)
            elif action.action_type == OperatorActionType.CLARIFY:
                return self._handle_clarify(action)
            elif action.action_type == OperatorActionType.PAUSE:
                return self._handle_pause(action)
            elif action.action_type == OperatorActionType.RESUME:
                return self._handle_resume(action)
            elif action.action_type == OperatorActionType.ABORT:
                return self._handle_abort(action)
            else:
                return OperatorResolution(
                    action_id=action.action_id,
                    request_id=action.request_id,
                    success=False,
                    status="unsupported_action",
                    message=f"Unsupported operator action type: {action.action_type}",
                )

    def _handle_approve(self, action: OperatorAction) -> OperatorResolution:
        """Handle operator approval of a sensitive action."""
        try:
            app_req = self.approval_gateway.approve(approval_id=action.request_id)
            app_req.metadata["approver"] = action.operator_id
            if action.decision_rationale:
                app_req.metadata["approval_reason"] = action.decision_rationale

            # Emit streaming event
            self.streaming_gateway.create_and_publish(
                session_id=action.session_id,
                event_type=StreamEventType.APPROVAL_RESOLVED,
                data={
                    "approval_id": app_req.approval_id,
                    "task_id": app_req.task_id,
                    "status": app_req.status.value if hasattr(app_req.status, "value") else str(app_req.status),
                    "approver": action.operator_id,
                    "rationale": action.decision_rationale,
                },
                task_id=app_req.task_id,
                step_id=app_req.step_id,
            )
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=True,
                status="approved",
                message=f"Approval request '{action.request_id}' approved.",
                details={"approval_id": app_req.approval_id, "task_id": app_req.task_id},
            )
        except Exception as e:
            logger.error("Failed to approve '%s': %s", action.request_id, e)
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=False,
                status="approval_failed",
                message=str(e),
            )

    def _handle_reject(self, action: OperatorAction) -> OperatorResolution:
        """Handle operator rejection of a sensitive action."""
        try:
            app_req = self.approval_gateway.reject(
                approval_id=action.request_id,
                reason=action.decision_rationale or "Rejected by operator via OperatorBridge",
            )
            app_req.metadata["rejecter"] = action.operator_id

            self.streaming_gateway.create_and_publish(
                session_id=action.session_id,
                event_type=StreamEventType.APPROVAL_RESOLVED,
                data={
                    "approval_id": app_req.approval_id,
                    "task_id": app_req.task_id,
                    "status": app_req.status.value if hasattr(app_req.status, "value") else str(app_req.status),
                    "rejecter": action.operator_id,
                    "rationale": action.decision_rationale,
                },
                task_id=app_req.task_id,
                step_id=app_req.step_id,
            )
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=True,
                status="rejected",
                message=f"Approval request '{action.request_id}' rejected.",
                details={"approval_id": app_req.approval_id, "task_id": app_req.task_id},
            )
        except Exception as e:
            logger.error("Failed to reject '%s': %s", action.request_id, e)
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=False,
                status="rejection_failed",
                message=str(e),
            )

    def _handle_clarify(self, action: OperatorAction) -> OperatorResolution:
        """Handle operator submission of clarification data."""
        try:
            resp = self.clarification_gateway.submit_response(
                clarification_id=action.request_id,
                response_data=action.clarification_payload,
                metadata=action.metadata,
            )
            if resp is None:
                return OperatorResolution(
                    action_id=action.action_id,
                    request_id=action.request_id,
                    success=False,
                    status="not_found_or_expired",
                    message=f"Clarification request '{action.request_id}' not found or already completed/expired.",
                )

            self.streaming_gateway.create_and_publish(
                session_id=action.session_id,
                event_type=StreamEventType.CLARIFICATION_RESOLVED,
                data={
                    "clarification_id": resp.clarification_id,
                    "response_data": canonical_session_value(resp.response_data),
                    "operator_id": action.operator_id,
                },
            )
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=True,
                status="clarified",
                message=f"Clarification '{action.request_id}' successfully resolved.",
                details={"clarification_id": resp.clarification_id},
            )
        except Exception as e:
            logger.error("Failed to clarify '%s': %s", action.request_id, e)
            return OperatorResolution(
                action_id=action.action_id,
                request_id=action.request_id,
                success=False,
                status="clarification_failed",
                message=str(e),
            )

    def _handle_pause(self, action: OperatorAction) -> OperatorResolution:
        """Handle pausing a running supervisor task or session."""
        task_id = action.request_id
        if self.supervisor is not None and hasattr(self.supervisor, "pause_task"):
            success = self.supervisor.pause_task(task_id)
            if success:
                self.streaming_gateway.create_and_publish(
                    session_id=action.session_id,
                    event_type=StreamEventType.STEP_PROGRESS,
                    data={"task_id": task_id, "status": "paused", "operator_id": action.operator_id},
                    task_id=task_id,
                )
                return OperatorResolution(
                    action_id=action.action_id,
                    request_id=task_id,
                    success=True,
                    status="paused",
                    message=f"Task '{task_id}' paused successfully.",
                )
        return OperatorResolution(
            action_id=action.action_id,
            request_id=task_id,
            success=False,
            status="pause_failed",
            message=f"Supervisor not configured or unable to pause task '{task_id}'.",
        )

    def _handle_resume(self, action: OperatorAction) -> OperatorResolution:
        """Handle resuming a paused supervisor task."""
        task_id = action.request_id
        if self.supervisor is not None and hasattr(self.supervisor, "resume_task"):
            success = self.supervisor.resume_task(task_id)
            if success:
                self.streaming_gateway.create_and_publish(
                    session_id=action.session_id,
                    event_type=StreamEventType.STEP_PROGRESS,
                    data={"task_id": task_id, "status": "resumed", "operator_id": action.operator_id},
                    task_id=task_id,
                )
                return OperatorResolution(
                    action_id=action.action_id,
                    request_id=task_id,
                    success=True,
                    status="resumed",
                    message=f"Task '{task_id}' resumed successfully.",
                )
        return OperatorResolution(
            action_id=action.action_id,
            request_id=task_id,
            success=False,
            status="resume_failed",
            message=f"Supervisor not configured or unable to resume task '{task_id}'.",
        )

    def _handle_abort(self, action: OperatorAction) -> OperatorResolution:
        """Handle aborting a task, approval, or clarification."""
        target_id = action.request_id
        if self.supervisor is not None and hasattr(self.supervisor, "cancel_task"):
            self.supervisor.cancel_task(target_id)
        # Attempt to reject approval if exists
        try:
            self.approval_gateway.reject(target_id, rejecter=action.operator_id, reason="Aborted by operator")
        except Exception:
            pass

        self.streaming_gateway.create_and_publish(
            session_id=action.session_id,
            event_type=StreamEventType.STEP_COMPLETED,
            data={"task_id": target_id, "status": "aborted", "operator_id": action.operator_id},
            task_id=target_id,
        )
        return OperatorResolution(
            action_id=action.action_id,
            request_id=target_id,
            success=True,
            status="aborted",
            message=f"Target '{target_id}' aborted by operator.",
        )

    def push_pending_approval(
        self,
        approval_request: ApprovalRequest,
        session_id: str = "default",
    ) -> None:
        """Publish an APPROVAL_REQUIRED event to notify operator via stream."""
        clean_sid = validate_session_id(session_id)
        self.streaming_gateway.create_and_publish(
            session_id=clean_sid,
            event_type=StreamEventType.APPROVAL_REQUIRED,
            data={
                "approval_id": approval_request.approval_id,
                "task_id": approval_request.task_id,
                "plan_id": approval_request.plan_id,
                "step_id": approval_request.step_id,
                "skill_name": approval_request.skill_name,
                "reason": approval_request.reason,
                "declared_tools": list(approval_request.declared_tools),
                "plan_fingerprint": approval_request.plan_fingerprint,
                "timeout_seconds": self.default_operator_timeout,
            },
            task_id=approval_request.task_id,
            step_id=approval_request.step_id,
        )

    def push_pending_clarification(
        self,
        clarification_request: ClarificationRequest,
        session_id: str = "default",
    ) -> None:
        """Publish a CLARIFICATION_REQUIRED event to notify operator via stream."""
        clean_sid = validate_session_id(session_id)
        self.streaming_gateway.create_and_publish(
            session_id=clean_sid,
            event_type=StreamEventType.CLARIFICATION_REQUIRED,
            data={
                "clarification_id": clarification_request.clarification_id,
                "goal_id": clarification_request.goal_id,
                "task_id": clarification_request.task_id,
                "question": clarification_request.question,
                "options": list(clarification_request.options),
                "clarification_type": (
                    clarification_request.clarification_type.value
                    if hasattr(clarification_request.clarification_type, "value")
                    else str(clarification_request.clarification_type)
                ),
                "timeout_seconds": clarification_request.timeout_seconds,
            },
            task_id=clarification_request.task_id,
            goal_id=clarification_request.goal_id,
        )

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        """List all pending approval requests."""
        with self._lock:
            # ApprovalGateway internal requests dict
            requests = getattr(self.approval_gateway, "_requests", {})
            return [
                {
                    "approval_id": r.approval_id,
                    "task_id": r.task_id,
                    "step_id": r.step_id,
                    "skill_name": r.skill_name,
                    "reason": r.reason,
                    "created_at": r.created_at,
                }
                for r in requests.values()
                if (hasattr(r, "status") and (r.status == ApprovalStatus.PENDING or r.status == "pending"))
            ]

    def list_pending_clarifications(self) -> list[dict[str, Any]]:
        """List all pending clarification requests."""
        with self._lock:
            requests = getattr(self.clarification_gateway, "_requests", {})
            return [
                {
                    "clarification_id": r.clarification_id,
                    "goal_id": r.goal_id,
                    "task_id": r.task_id,
                    "question": r.question,
                    "options": list(r.options),
                    "created_at": r.created_at,
                    "timeout_seconds": r.timeout_seconds,
                }
                for r in requests.values()
                if (hasattr(r, "status") and (r.status == ClarificationStatus.PENDING or r.status == "pending"))
            ]

    def get_audit_log(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Query recorded operator actions."""
        clean_sid = validate_session_id(session_id) if session_id is not None else None
        with self._lock:
            if clean_sid is not None:
                return [a for a in self._audit_log if a["session_id"] == clean_sid]
            return list(self._audit_log)
