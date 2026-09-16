"""M52 — Bounded Background Task Worker.

Implements a bounded worker thread pool that polls PostgreSQL for pending tasks,
atomically acquires leases using SELECT ... FOR UPDATE SKIP LOCKED, executes tasks
via WorkflowOrchestrator, handles cooperative cancellations, and manages timeouts.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from core.background.workflow_orchestrator import WorkflowOrchestrator
from core.cancellation import CancellationToken
from core.repositories.base import BaseApprovalRepository, BaseTaskRepository

logger = logging.getLogger("aura.background.task_worker")


class BackgroundTaskWorker:
    """Bounded in-process worker pool for asynchronous background tasks."""

    def __init__(
        self,
        task_repo: BaseTaskRepository,
        approval_repo: BaseApprovalRepository,
        orchestrator: WorkflowOrchestrator,
        concurrency: int = 4,
        poll_interval_ms: int = 500,
        worker_id: str | None = None,
    ) -> None:
        self.task_repo = task_repo
        self.approval_repo = approval_repo
        self.orchestrator = orchestrator
        self.concurrency = max(1, concurrency)
        self.poll_interval = max(0.05, poll_interval_ms / 1000.0)
        self.worker_id = worker_id or f"worker_{threading.get_ident()}"

        self._stop_event = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self._monitor_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._active_tasks: dict[str, Future[Any]] = {}
        self._cancellation_tokens: dict[str, CancellationToken] = {}
        self._last_sweep_time = 0.0

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and self._monitor_thread is not None and self._monitor_thread.is_alive()

    def start(self) -> None:
        """Start the worker threadpool and polling loop."""
        with self._lock:
            if self.is_running:
                logger.warning("BackgroundTaskWorker is already running")
                return

            self._stop_event.clear()
            self._pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="aura-bg-worker")

            # Initial startup crash recovery sweep
            try:
                recovered = self.task_repo.recover_stale_tasks(stale_threshold_seconds=180.0)
                if recovered:
                    logger.info(f"Startup crash recovery sweep recovered {len(recovered)} tasks: {recovered}")
                expired = self.approval_repo.expire_stale_approvals()
                if expired:
                    logger.info(f"Startup approval sweep expired {len(expired)} approvals")
            except Exception as e:
                logger.warning(f"Error during startup worker recovery sweep: {e}")

            self._monitor_thread = threading.Thread(target=self._run_loop, name="aura-worker-monitor", daemon=True)
            self._monitor_thread.start()
            logger.info(f"BackgroundTaskWorker started (concurrency={self.concurrency}, poll_interval={self.poll_interval}s)")

    def stop(self, timeout: float = 10.0) -> None:
        """Gracefully stop the worker pool."""
        logger.info("Initiating BackgroundTaskWorker shutdown...")
        self._stop_event.set()

        # Signal cancellation to all currently running tasks
        with self._lock:
            for tid, token in self._cancellation_tokens.items():
                token.cancel()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=timeout)

        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None

        logger.info("BackgroundTaskWorker stopped cleanly.")

    def cancel_task(self, task_id: str, user_id: str) -> bool:
        """Cancel an in-flight or queued task."""
        tid = task_id.strip()
        cancelled_in_repo = self.task_repo.cancel_task(tid, user_id)
        with self._lock:
            if tid in self._cancellation_tokens:
                self._cancellation_tokens[tid].cancel()
        return cancelled_in_repo

    def _run_loop(self) -> None:
        """Continuous polling loop."""
        while not self._stop_event.is_set():
            # Clean up completed futures
            with self._lock:
                done_tasks = [tid for tid, fut in self._active_tasks.items() if fut.done()]
                for tid in done_tasks:
                    del self._active_tasks[tid]
                    self._cancellation_tokens.pop(tid, None)
                active_count = len(self._active_tasks)

            # Periodic sweep for orphaned tasks and expired approvals (every 30s)
            now = time.time()
            if now - self._last_sweep_time > 30.0:
                self._last_sweep_time = now
                try:
                    self.task_repo.recover_stale_tasks(stale_threshold_seconds=300.0)
                    self.approval_repo.expire_stale_approvals()
                except Exception as se:
                    logger.debug(f"Periodic maintenance sweep notice: {se}")

            # If capacity available, attempt to acquire next pending task
            if active_count < self.concurrency and self._pool is not None:
                try:
                    task = self.task_repo.acquire_next_pending_task(worker_id=self.worker_id)
                    if task:
                        tid = task["id"]
                        token = CancellationToken()
                        with self._lock:
                            self._cancellation_tokens[tid] = token
                            fut = self._pool.submit(self._execute_single_task, task, token)
                            self._active_tasks[tid] = fut
                        continue  # Check for another task immediately if we have capacity
                except Exception as e:
                    logger.error(f"Error while acquiring background task: {e}", exc_info=True)

            self._stop_event.wait(self.poll_interval)

    def _execute_single_task(self, task_record: dict[str, Any], token: CancellationToken) -> None:
        """Execute single leased task."""
        tid = task_record["id"]
        try:
            self.orchestrator.execute_task(task_record, cancellation_requested=token.is_cancelled)
        except Exception as e:
            logger.error(f"Unhandled exception in background execution for task '{tid}': {e}", exc_info=True)
            self.task_repo.update_task_status(
                tid,
                task_record["user_id"],
                "failed",
                error_message=f"Internal worker exception: {e}",
            )
