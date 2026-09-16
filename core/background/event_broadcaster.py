"""M52 — Task Event Broadcaster for Server-Sent Events (SSE).

Provides thread-safe pub/sub event distribution per task_id with secret scrubbing,
keep-alive comment pings, and bounded subscriber queues.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from core.security_scrubber import scrub_dict

logger = logging.getLogger("aura.background.event_broadcaster")


class TaskEventBroadcaster:
    """Thread-safe in-memory event broadcaster for task execution streams."""

    def __init__(self, max_queue_size: int = 100) -> None:
        self._lock = threading.RLock()
        # task_id -> list of subscriber queues
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self.max_queue_size = max_queue_size

    def subscribe(self, task_id: str) -> queue.Queue[dict[str, Any]]:
        """Subscribe to events for a specific task_id."""
        with self._lock:
            q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
            self._subscribers.setdefault(task_id.strip(), []).append(q)
            return q

    def unsubscribe(self, task_id: str, q: queue.Queue[dict[str, Any]]) -> None:
        """Deregister subscriber queue."""
        with self._lock:
            tid = task_id.strip()
            if tid in self._subscribers:
                try:
                    self._subscribers[tid].remove(q)
                except ValueError:
                    pass
                if not self._subscribers[tid]:
                    del self._subscribers[tid]

    def publish(self, task_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast scrubbed event to all active subscribers for task_id."""
        tid = task_id.strip()
        safe_data = scrub_dict(data) if isinstance(data, dict) else {"message": str(data)}
        msg = {
            "event": event_type.strip(),
            "data": safe_data,
            "task_id": tid,
            "timestamp": time.time(),
        }

        with self._lock:
            subscribers = list(self._subscribers.get(tid, []))

        for q in subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                logger.debug(f"Subscriber queue full for task '{tid}'; dropping oldest event")
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    @staticmethod
    def format_sse(event_type: str, data: dict[str, Any]) -> str:
        """Format event and data into standard SSE wire representation."""
        safe_data = scrub_dict(data) if isinstance(data, dict) else {"message": str(data)}
        body = json.dumps(safe_data, default=str)
        return f"event: {event_type.strip()}\ndata: {body}\n\n"

    @staticmethod
    def format_ping() -> str:
        """Format keep-alive comment ping."""
        return ": ping\n\n"
