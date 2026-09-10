import collections
import json
import logging
import queue
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.config import settings
from core.session_types import (
    StreamEvent,
    StreamEventType,
    sanitize_session_metadata,
    validate_session_id,
)

logger = logging.getLogger("aura.streaming_gateway")


@dataclass
class Subscription:
    """Subscription record for an active stream consumer."""

    subscriber_id: str
    queue: queue.Queue
    session_id: str | None = None
    event_types: set[StreamEventType] | None = None
    created_at: float = field(default_factory=time.time)
    dropped_events_count: int = 0

    def matches(self, event: StreamEvent) -> bool:
        """Check whether an emitted event satisfies this subscription's filters."""
        if self.session_id is not None and event.session_id != self.session_id:
            return False
        if self.event_types is not None and event.event_type not in self.event_types:
            return False
        return True


class StreamingGateway:
    """Pub/Sub real-time event streaming gateway with per-subscriber queues, SSE/WS framing, and replay buffer."""

    def __init__(
        self,
        max_queue_size: int | None = None,
        replay_buffer_size: int | None = None,
        max_payload_chars: int | None = None,
    ):
        self.max_queue_size: int = (
            int(max_queue_size)
            if max_queue_size is not None
            else getattr(settings, "aura_streaming_queue_max_size", 1000)
        )
        self.replay_buffer_size: int = (
            int(replay_buffer_size)
            if replay_buffer_size is not None
            else getattr(settings, "aura_streaming_replay_buffer_size", 1000)
        )
        self.max_payload_chars: int = (
            int(max_payload_chars)
            if max_payload_chars is not None
            else getattr(settings, "aura_max_event_payload_chars", 50000)
        )

        self._lock = threading.RLock()
        self._subscribers: dict[str, Subscription] = {}
        self._replay_buffer: collections.deque[StreamEvent] = collections.deque(maxlen=self.replay_buffer_size)

    def subscribe(
        self,
        subscriber_id: str | None = None,
        session_id: str | None = None,
        event_types: set[StreamEventType] | Sequence[StreamEventType | str] | None = None,
        last_event_id: str | None = None,
    ) -> tuple[str, queue.Queue]:
        """Subscribe to events with optional session and event-type filtering and Last-Event-ID replay."""
        sub_id = subscriber_id.strip() if subscriber_id and subscriber_id.strip() else str(uuid4())
        clean_sid = validate_session_id(session_id) if session_id is not None else None

        parsed_types: set[StreamEventType] | None = None
        if event_types is not None:
            parsed_types = set()
            for et in event_types:
                if isinstance(et, str):
                    parsed_types.add(StreamEventType(et))
                elif isinstance(et, StreamEventType):
                    parsed_types.add(et)

        sub_queue: queue.Queue = queue.Queue(maxsize=self.max_queue_size)
        sub = Subscription(
            subscriber_id=sub_id,
            queue=sub_queue,
            session_id=clean_sid,
            event_types=parsed_types,
        )

        with self._lock:
            self._subscribers[sub_id] = sub

            # If last_event_id is specified, replay missed events from buffer
            if last_event_id:
                replayed = self._get_events_after(last_event_id, session_id=clean_sid, event_types=parsed_types)
                for ev in replayed:
                    try:
                        sub_queue.put_nowait(ev)
                    except queue.Full:
                        sub.dropped_events_count += 1
                        break

            return sub_id, sub_queue

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Remove an active subscription."""
        clean_id = subscriber_id.strip() if subscriber_id else ""
        with self._lock:
            if clean_id in self._subscribers:
                del self._subscribers[clean_id]
                return True
            return False

    def publish(self, event: StreamEvent) -> int:
        """Publish an event to all matching subscriber queues and append to replay buffer."""
        if not isinstance(event, StreamEvent):
            raise TypeError("event must be an instance of StreamEvent.")

        with self._lock:
            self._replay_buffer.append(event)
            delivered_count = 0

            for sub_id, sub in list(self._subscribers.items()):
                if sub.matches(event):
                    try:
                        sub.queue.put_nowait(event)
                        delivered_count += 1
                    except queue.Full:
                        # Drop oldest or drop new to protect agent thread from blocking
                        try:
                            _ = sub.queue.get_nowait()
                            sub.queue.put_nowait(event)
                            sub.dropped_events_count += 1
                            delivered_count += 1
                        except Exception:
                            sub.dropped_events_count += 1

            return delivered_count

    def create_and_publish(
        self,
        session_id: str,
        event_type: StreamEventType | str,
        data: dict[str, Any],
        step_id: str | None = None,
        goal_id: str | None = None,
        task_id: str | None = None,
    ) -> StreamEvent:
        """Create and publish a stream event in one call."""
        eff_type = StreamEventType(event_type) if isinstance(event_type, str) else event_type
        event = StreamEvent(
            session_id=session_id,
            event_type=eff_type,
            data=data,
            step_id=step_id,
            goal_id=goal_id,
            task_id=task_id,
        )
        self.publish(event)
        return event

    def _get_events_after(
        self,
        last_event_id: str,
        session_id: str | None = None,
        event_types: set[StreamEventType] | None = None,
    ) -> list[StreamEvent]:
        """Find events occurring after the specified event_id in the replay buffer."""
        events = list(self._replay_buffer)
        idx = -1
        for i, ev in enumerate(events):
            if ev.event_id == last_event_id:
                idx = i
                break

        missed = events[idx + 1 :] if idx != -1 else events
        results: list[StreamEvent] = []
        for ev in missed:
            if session_id is not None and ev.session_id != session_id:
                continue
            if event_types is not None and ev.event_type not in event_types:
                continue
            results.append(ev)
        return results

    def get_replay_events(
        self,
        session_id: str | None = None,
        since_event_id: str | None = None,
        limit: int = 100,
    ) -> list[StreamEvent]:
        """Query historical events from the replay buffer."""
        with self._lock:
            if since_event_id:
                events = self._get_events_after(since_event_id, session_id=session_id)
            else:
                events = [ev for ev in self._replay_buffer if session_id is None or ev.session_id == session_id]
            return events[-limit:]

    def format_sse(self, event: StreamEvent) -> str:
        """Format an event as SSE chunk."""
        return event.to_sse()

    def format_ws(self, event: StreamEvent) -> str:
        """Format an event as WebSocket message payload."""
        return event.to_ws_message()

    @property
    def active_subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
