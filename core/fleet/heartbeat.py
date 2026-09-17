"""M55 — Worker Heartbeat & Liveness Management.

Maintains periodic heartbeat pings to PostgreSQL/In-Memory repository,
renews active leases in batch, and detects missed heartbeats to trigger
self-degradation (UNHEALTHY) when database connectivity or event loops lag.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Optional

from core.fleet.types import WorkerStatus
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_fleet import BaseFleetRepository

logger = logging.getLogger("aura.fleet.heartbeat")


class HeartbeatManager:
    """Manages worker liveness heartbeats and periodic lease renewal."""

    def __init__(
        self,
        fleet_repo: BaseFleetRepository,
        worker_id: str,
        incarnation_token: str,
        heartbeat_interval_seconds: float = 5.0,
        missed_threshold: int = 3,
        lease_duration_seconds: float = 30.0,
    ) -> None:
        self.fleet_repo = fleet_repo
        self.worker_id = worker_id
        self.incarnation_token = incarnation_token
        self.interval = max(0.1, heartbeat_interval_seconds)
        self.missed_threshold = max(1, missed_threshold)
        self.lease_duration = max(5.0, lease_duration_seconds)

        self._consecutive_failures = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_successful_heartbeat: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def last_heartbeat(self) -> Optional[datetime]:
        return self._last_successful_heartbeat

    def start(self) -> None:
        """Start the background heartbeat thread."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._consecutive_failures = 0
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"aura-hb-{self.worker_id[:8]}",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"HeartbeatManager started for worker {self.worker_id} (interval={self.interval}s)")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the heartbeat thread cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info(f"HeartbeatManager stopped for worker {self.worker_id}")

    def pulse_once(self) -> bool:
        """Emit a single heartbeat and renew active leases."""
        try:
            hb_ok = self.fleet_repo.update_worker_heartbeat(self.worker_id, self.incarnation_token)
            if hb_ok:
                self._last_successful_heartbeat = datetime.now(timezone.utc)
                self._consecutive_failures = 0
                # Renew leases
                try:
                    self.fleet_repo.batch_renew_worker_leases(
                        self.worker_id,
                        self.incarnation_token,
                        self.lease_duration,
                    )
                except Exception as e:
                    logger.debug(f"Batch lease renewal notice: {e}")
                return True
            else:
                self._handle_failure("Heartbeat update returned false (worker record missing or incarnation mismatch)")
                return False
        except Exception as e:
            self._handle_failure(f"Exception during heartbeat pulse: {e}")
            return False

    def _handle_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        logger.warning(
            f"Worker {self.worker_id} heartbeat failure ({self._consecutive_failures}/{self.missed_threshold}): {reason}"
        )
        if self._consecutive_failures >= self.missed_threshold:
            try:
                self.fleet_repo.update_worker_status(
                    self.worker_id,
                    self.incarnation_token,
                    WorkerStatus.UNHEALTHY,
                )
                logger.error(f"Worker {self.worker_id} marked UNHEALTHY after {self._consecutive_failures} missed heartbeats")
            except Exception as e:
                logger.error(f"Failed to update worker status to UNHEALTHY: {e}")

    def _heartbeat_loop(self) -> None:
        """Main periodic heartbeat loop."""
        while not self._stop_event.is_set():
            self.pulse_once()
            self._stop_event.wait(timeout=self.interval)
