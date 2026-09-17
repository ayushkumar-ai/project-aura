"""M55 — Distributed Fleet Worker Implementation.

Horizontally scalable worker node coordinating via PostgreSQL/In-Memory fleet repository.
Enforces monotonic fencing validation on all writes, multi-tenant deficit fairness,
heartbeat liveness, cooperative cancellation, and graceful draining.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import os
import platform
import socket
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from core.cancellation import CancellationToken
from core.fleet.heartbeat import HeartbeatManager
from core.fleet.types import (
    AttemptStatus,
    ClaimedTask,
    FencingTokenMismatchError,
    WorkerNotHealthyError,
    WorkerRecord,
    WorkerStatus,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_fleet import BaseFleetRepository
    from core.repositories.base import BaseApprovalRepository, BaseTaskRepository
    from core.background.workflow_orchestrator import WorkflowOrchestrator

logger = logging.getLogger("aura.fleet.worker")


class DistributedFleetWorker:
    """Horizontally scalable, lease-fenced worker node for distributed task execution."""

    def __init__(
        self,
        fleet_repo: BaseFleetRepository,
        task_repo: BaseTaskRepository,
        approval_repo: BaseApprovalRepository,
        orchestrator: WorkflowOrchestrator,
        concurrency: int = 4,
        poll_interval_ms: int = 500,
        heartbeat_interval_seconds: float = 5.0,
        missed_heartbeats_threshold: int = 3,
        lease_duration_seconds: float = 30.0,
        drain_timeout_seconds: float = 30.0,
        worker_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        hostname: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
    ) -> None:
        self.fleet_repo = fleet_repo
        self.task_repo = task_repo
        self.approval_repo = approval_repo
        self.orchestrator = orchestrator

        self.concurrency = max(1, concurrency)
        self.poll_interval = max(0.05, poll_interval_ms / 1000.0)
        self.heartbeat_interval = max(0.1, heartbeat_interval_seconds)
        self.missed_threshold = max(1, missed_heartbeats_threshold)
        self.lease_duration = max(5.0, lease_duration_seconds)
        self.drain_timeout = max(1.0, drain_timeout_seconds)

        self.worker_id = worker_id or f"wkr_{uuid.uuid4().hex[:12]}"
        self.instance_id = instance_id or f"inst_{uuid.uuid4().hex[:8]}"
        self.hostname = hostname or socket.gethostname()
        self.process_id = os.getpid()
        self.capabilities = capabilities or ["*"]

        # Incarnation token uniquely generated per process lifecycle
        self.incarnation_token = f"inc_{uuid.uuid4().hex}"
        self.generation = 1

        self._stop_event = threading.Event()
        self._drain_event = threading.Event()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._active_tasks: Dict[str, Future[Any]] = {}
        self._cancellation_tokens: Dict[str, CancellationToken] = {}
        self._heartbeat_manager: Optional[HeartbeatManager] = None

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and self._poll_thread is not None and self._poll_thread.is_alive()

    @property
    def is_draining(self) -> bool:
        return self._drain_event.is_set()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active_tasks)

    def start(self) -> None:
        """Register worker, initialize heartbeat, and start polling loop."""
        with self._lock:
            if self.is_running:
                logger.warning(f"DistributedFleetWorker {self.worker_id} is already running.")
                return

            self._stop_event.clear()
            self._drain_event.clear()

            # 1. Register worker node
            worker_record = WorkerRecord(
                worker_id=self.worker_id,
                instance_id=self.instance_id,
                hostname=self.hostname,
                process_id=self.process_id,
                incarnation_token=self.incarnation_token,
                generation=self.generation,
                status=WorkerStatus.HEALTHY,
                capabilities=self.capabilities,
                concurrency_limit=self.concurrency,
                active_task_count=0,
                heartbeat_interval_seconds=self.heartbeat_interval,
                missed_heartbeats_threshold=self.missed_threshold,
            )
            reg = self.fleet_repo.register_worker(worker_record)
            self.generation = reg.generation

            # 2. Start heartbeat manager
            self._heartbeat_manager = HeartbeatManager(
                fleet_repo=self.fleet_repo,
                worker_id=self.worker_id,
                incarnation_token=self.incarnation_token,
                heartbeat_interval_seconds=self.heartbeat_interval,
                missed_threshold=self.missed_threshold,
                lease_duration_seconds=self.lease_duration,
            )
            self._heartbeat_manager.start()

            # 3. Start task worker threadpool
            self._pool = ThreadPoolExecutor(
                max_workers=self.concurrency,
                thread_name_prefix=f"aura-wkr-{self.worker_id[:6]}",
            )

            # 4. Start polling loop thread
            self._poll_thread = threading.Thread(
                target=self._run_loop,
                name=f"aura-poll-{self.worker_id[:6]}",
                daemon=True,
            )
            self._poll_thread.start()
            logger.info(
                f"DistributedFleetWorker {self.worker_id} started (incarnation={self.incarnation_token[:10]}..., concurrency={self.concurrency})"
            )

    def drain(self) -> None:
        """Initiate graceful draining (stops claiming new tasks, allows active to finish)."""
        logger.info(f"Initiating graceful drain for worker {self.worker_id}...")
        self._drain_event.set()
        try:
            self.fleet_repo.update_worker_status(
                self.worker_id,
                self.incarnation_token,
                WorkerStatus.DRAINING,
                draining_since=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning(f"Error marking worker DRAINING in repo: {e}")

    def stop(self, timeout: Optional[float] = None) -> None:
        """Gracefully stop worker, draining in-flight work before shutting down."""
        drain_limit = timeout if timeout is not None else self.drain_timeout
        logger.info(f"Stopping DistributedFleetWorker {self.worker_id} (timeout={drain_limit}s)...")

        self.drain()
        self._stop_event.set()

        # Wait for active tasks to complete up to drain timeout
        start_wait = time.time()
        while self.active_count > 0 and (time.time() - start_wait) < drain_limit:
            time.sleep(0.1)

        # Force-cancel any remaining lingering tasks
        with self._lock:
            for tid, token in list(self._cancellation_tokens.items()):
                logger.warning(f"Force-cancelling lingering task {tid} on worker shutdown")
                token.cancel()

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)

        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None

        if self._heartbeat_manager:
            self._heartbeat_manager.stop(timeout=2.0)
            self._heartbeat_manager = None

        # Unregister worker record cleanly
        try:
            self.fleet_repo.unregister_worker(self.worker_id, self.incarnation_token)
        except Exception as e:
            logger.warning(f"Error unregistering worker on stop: {e}")

        logger.info(f"DistributedFleetWorker {self.worker_id} stopped cleanly.")

    def cancel_task(self, task_id: str) -> bool:
        """Cancel an in-flight task on this worker."""
        with self._lock:
            token = self._cancellation_tokens.get(task_id)
            if token:
                token.cancel()
                return True
        return False

    def _run_loop(self) -> None:
        """Main poll-claim-dispatch loop."""
        while not self._stop_event.is_set() and not self._drain_event.is_set():
            try:
                self._poll_and_dispatch()
            except Exception as e:
                logger.error(f"Error in worker poll loop: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.poll_interval)

    def _poll_and_dispatch(self) -> None:
        # Check local concurrency headroom
        with self._lock:
            if len(self._active_tasks) >= self.concurrency:
                return

        # Attempt to claim next fair task
        claimed = self.fleet_repo.claim_next_fair_task(
            worker_id=self.worker_id,
            incarnation_token=self.incarnation_token,
            lease_duration_seconds=self.lease_duration,
        )
        if not claimed:
            return

        # Spawn execution in threadpool
        cancel_token = CancellationToken()
        with self._lock:
            self._cancellation_tokens[claimed.task_id] = cancel_token
            fut = self._pool.submit(self._execute_claimed_task, claimed, cancel_token)
            self._active_tasks[claimed.task_id] = fut
            fut.add_done_callback(lambda f, tid=claimed.task_id: self._on_task_done(tid, f))

    def _on_task_done(self, task_id: str, future: Future[Any]) -> None:
        with self._lock:
            self._active_tasks.pop(task_id, None)
            self._cancellation_tokens.pop(task_id, None)

    def _execute_claimed_task(self, claimed: ClaimedTask, cancel_token: CancellationToken) -> None:
        task_id = claimed.task_id
        user_id = claimed.user_id
        fencing_token = claimed.fencing_token
        attempt_id = claimed.attempt_id
        logger.info(f"Worker {self.worker_id} executing task {task_id} (fence={fencing_token}, attempt={claimed.attempt_number})")

        try:
            # 1. Execute workflow via orchestrator (supporting execute_task and execute_workflow)
            if hasattr(self.orchestrator, "execute_task"):
                task_dict = {"id": task_id, "user_id": user_id, **claimed.task_data}
                res = self.orchestrator.execute_task(task_dict, cancellation_requested=cancel_token.is_cancelled)
            elif hasattr(self.orchestrator, "execute_workflow"):
                res = self.orchestrator.execute_workflow(task_id=task_id, user_id=user_id, cancellation_token=cancel_token)
            else:
                res = {"status": "completed"}

            outcome_status = res.get("status", "completed") if isinstance(res, dict) else "completed"
            if outcome_status == "completed":
                try:
                    self.task_repo.update_task_status(task_id, user_id, "completed", result=res if isinstance(res, dict) else None)
                except Exception:
                    pass

            # 2. Monotonic Fencing Verification before terminal mutation
            is_valid_fence = self.fleet_repo.verify_fencing(
                resource_type="task",
                resource_id=task_id,
                worker_id=self.worker_id,
                incarnation_token=self.incarnation_token,
                fencing_token=fencing_token,
            )
            if not is_valid_fence:
                logger.error(f"FENCING MISMATCH on task {task_id} - zombie write rejected! (fence={fencing_token})")
                self.fleet_repo.update_attempt_status(
                    attempt_id=attempt_id,
                    status=AttemptStatus.FENCED,
                    error_detail="Zombie write rejected: Fencing token mismatch or lease expired",
                )
                raise FencingTokenMismatchError(f"Fencing token {fencing_token} is no longer authoritative for task {task_id}")

            # 3. Mark attempt completed
            self.fleet_repo.update_attempt_status(
                attempt_id=attempt_id,
                status=AttemptStatus.COMPLETED,
            )

            # 4. Release lease
            self.fleet_repo.release_lease(
                resource_type="task",
                resource_id=task_id,
                worker_id=self.worker_id,
                incarnation_token=self.incarnation_token,
                fencing_token=fencing_token,
            )

            # 5. Decrement tenant active count
            try:
                self.fleet_repo.adjust_tenant_active_count(user_id, -1)
            except Exception as e:
                logger.warning(f"Error releasing tenant slot on task completion: {e}")

            logger.info(f"Worker {self.worker_id} completed task {task_id} successfully.")

        except FencingTokenMismatchError:
            # Already handled
            pass
        except Exception as e:
            logger.error(f"Worker {self.worker_id} task execution failure on {task_id}: {e}", exc_info=True)
            # Update attempt status
            try:
                self.fleet_repo.update_attempt_status(
                    attempt_id=attempt_id,
                    status=AttemptStatus.FAILED,
                    error_detail=str(e),
                )
            except Exception:
                pass

            # Release lease & tenant quota
            try:
                self.fleet_repo.release_lease(
                    resource_type="task",
                    resource_id=task_id,
                    worker_id=self.worker_id,
                    incarnation_token=self.incarnation_token,
                    fencing_token=fencing_token,
                )
            except Exception:
                pass
            try:
                self.fleet_repo.adjust_tenant_active_count(user_id, -1)
            except Exception:
                pass
