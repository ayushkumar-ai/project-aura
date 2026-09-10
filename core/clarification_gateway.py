import logging
import threading
import time
from typing import Any
from uuid import uuid4

from app.config import settings
from core.scheduling_types import (
    ClarificationRequest,
    ClarificationResponse,
    ClarificationStatus,
    ClarificationType,
)

logger = logging.getLogger("aura.clarification_gateway")


class ClarificationGateway:
    """Interactive human disambiguation gateway managing structured clarification requests and responses."""

    def __init__(self, default_timeout_seconds: float | None = None):
        self.default_timeout_seconds = (
            default_timeout_seconds
            if default_timeout_seconds is not None
            else getattr(settings, "aura_clarification_timeout_seconds", 600.0)
        )
        self._lock = threading.RLock()
        # requests: clarification_id -> ClarificationRequest
        self._requests: dict[str, ClarificationRequest] = {}
        # responses: clarification_id -> ClarificationResponse
        self._responses: dict[str, ClarificationResponse] = {}

    def prune_timed_out_requests(self, current_time: float | None = None) -> int:
        """Identify and transition expired pending clarification requests to TIMED_OUT."""
        now = current_time if current_time is not None else time.time()
        timed_out_count = 0

        with self._lock:
            for cid, req in list(self._requests.items()):
                if req.status == ClarificationStatus.PENDING and req.is_expired(now):
                    updated = ClarificationRequest(
                        clarification_id=req.clarification_id,
                        goal_id=req.goal_id,
                        task_id=req.task_id,
                        question=req.question,
                        options=req.options,
                        clarification_type=req.clarification_type,
                        status=ClarificationStatus.TIMED_OUT,
                        created_at=req.created_at,
                        timeout_seconds=req.timeout_seconds,
                        metadata=dict(req.metadata),
                    )
                    self._requests[cid] = updated
                    timed_out_count += 1
        return timed_out_count

    def request_clarification(
        self,
        goal_id: str,
        task_id: str,
        question: str,
        options: list[str] | tuple[str, ...] = (),
        clarification_type: ClarificationType | str = ClarificationType.SINGLE_CHOICE,
        timeout_seconds: float | None = None,
        current_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClarificationRequest:
        """Create and register a pending clarification request for a goal."""
        clean_gid = str(goal_id).strip()
        if not clean_gid:
            raise ValueError("goal_id must be a non-empty string.")

        clean_tid = str(task_id).strip()
        if not clean_tid:
            raise ValueError("task_id must be a non-empty string.")

        clean_q = str(question).strip()
        if not clean_q:
            raise ValueError("question must be a non-empty string.")

        eff_type = ClarificationType(clarification_type) if isinstance(clarification_type, str) else clarification_type
        eff_timeout = float(timeout_seconds) if timeout_seconds is not None else self.default_timeout_seconds
        now = current_time if current_time is not None else time.time()

        req = ClarificationRequest(
            clarification_id=str(uuid4()),
            goal_id=clean_gid,
            task_id=clean_tid,
            question=clean_q,
            options=tuple(options),
            clarification_type=eff_type,
            status=ClarificationStatus.PENDING,
            created_at=now,
            timeout_seconds=eff_timeout,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            self._requests[req.clarification_id] = req
        return req

    def submit_response(
        self,
        clarification_id: str,
        response_data: Any,
        current_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClarificationResponse:
        """Submit a user response to a pending clarification request."""
        clean_cid = str(clarification_id).strip()
        now = current_time if current_time is not None else time.time()

        with self._lock:
            self.prune_timed_out_requests(now)
            req = self._requests.get(clean_cid)
            if not req:
                raise KeyError(f"Clarification request '{clean_cid}' not found.")

            if req.status == ClarificationStatus.TIMED_OUT:
                raise ValueError(f"Clarification request '{clean_cid}' has timed out.")

            if req.status != ClarificationStatus.PENDING:
                raise ValueError(f"Clarification request '{clean_cid}' is not pending (status: {req.status}).")

            # Validate response against options if provided
            if req.options and req.clarification_type == ClarificationType.SINGLE_CHOICE:
                if str(response_data).strip() not in req.options:
                    # Allow fuzzy/valid response but log notice
                    logger.info("Response '%s' not explicitly in options %s, ingesting as user input.", response_data, req.options)

            # Update request status to ANSWERED
            updated_req = ClarificationRequest(
                clarification_id=req.clarification_id,
                goal_id=req.goal_id,
                task_id=req.task_id,
                question=req.question,
                options=req.options,
                clarification_type=req.clarification_type,
                status=ClarificationStatus.ANSWERED,
                created_at=req.created_at,
                timeout_seconds=req.timeout_seconds,
                metadata=dict(req.metadata),
            )
            self._requests[clean_cid] = updated_req

            resp = ClarificationResponse(
                clarification_id=clean_cid,
                goal_id=req.goal_id,
                response_data=response_data,
                status=ClarificationStatus.ANSWERED,
                answered_at=now,
                metadata=dict(metadata or {}),
            )
            self._responses[clean_cid] = resp
            return resp

    def get_request(self, clarification_id: str) -> ClarificationRequest | None:
        """Retrieve a clarification request by ID."""
        clean_cid = str(clarification_id).strip()
        with self._lock:
            return self._requests.get(clean_cid)

    def get_response(self, clarification_id: str) -> ClarificationResponse | None:
        """Retrieve a clarification response by request ID."""
        clean_cid = str(clarification_id).strip()
        with self._lock:
            return self._responses.get(clean_cid)

    def get_pending_requests(self, goal_id: str | None = None, current_time: float | None = None) -> list[ClarificationRequest]:
        """List all currently pending clarification requests, optionally filtered by goal ID."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self.prune_timed_out_requests(now)
            reqs = [r for r in self._requests.values() if r.status == ClarificationStatus.PENDING]
            if goal_id:
                clean_gid = str(goal_id).strip()
                reqs = [r for r in reqs if r.goal_id == clean_gid]
            return reqs

    def cancel_request(self, clarification_id: str, reason: str = "") -> bool:
        """Cancel a pending clarification request."""
        clean_cid = str(clarification_id).strip()
        with self._lock:
            req = self._requests.get(clean_cid)
            if not req or req.status != ClarificationStatus.PENDING:
                return False

            meta = dict(req.metadata)
            if reason:
                meta["cancel_reason"] = reason

            updated = ClarificationRequest(
                clarification_id=req.clarification_id,
                goal_id=req.goal_id,
                task_id=req.task_id,
                question=req.question,
                options=req.options,
                clarification_type=req.clarification_type,
                status=ClarificationStatus.CANCELLED,
                created_at=req.created_at,
                timeout_seconds=req.timeout_seconds,
                metadata=meta,
            )
            self._requests[clean_cid] = updated
            return True
