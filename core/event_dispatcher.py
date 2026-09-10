import collections
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from app.config import settings
from core.goal import GoalObservation
from core.goal_scheduler import MultiGoalScheduler
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.scheduling_types import EventSubscription, ProactiveEvent

logger = logging.getLogger("aura.event_dispatcher")


class ProactiveEventDispatcher:
    """Asynchronous event broker and proactive trigger dispatcher for Project AURA."""

    def __init__(self, max_queue_size: int | None = None):
        self.max_queue_size = (
            max_queue_size
            if max_queue_size is not None
            else getattr(settings, "aura_event_queue_max_size", 1000)
        )
        self._lock = threading.RLock()

        # Subscriptions: subscription_id -> EventSubscription
        self._subscriptions: dict[str, EventSubscription] = {}

        # Event Queue: deque of ProactiveEvent
        self._event_queue: collections.deque[ProactiveEvent] = collections.deque(maxlen=self.max_queue_size)

    def subscribe(
        self,
        topic_pattern: str,
        goal_id: str,
        trigger_id: str | None = None,
        cooldown_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> EventSubscription:
        """Register a new event subscription for a goal."""
        sub = EventSubscription(
            subscription_id=str(uuid4()),
            topic_pattern=topic_pattern,
            goal_id=goal_id,
            trigger_id=trigger_id,
            created_at=time.time(),
            cooldown_seconds=cooldown_seconds,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._subscriptions[sub.subscription_id] = sub
        return sub

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove an existing event subscription."""
        clean_id = str(subscription_id).strip()
        with self._lock:
            return bool(self._subscriptions.pop(clean_id, None))

    def unsubscribe_all_for_goal(self, goal_id: str) -> int:
        """Remove all subscriptions registered for a goal."""
        clean_gid = str(goal_id).strip()
        with self._lock:
            to_remove = [sid for sid, s in self._subscriptions.items() if s.goal_id == clean_gid]
            for sid in to_remove:
                del self._subscriptions[sid]
            return len(to_remove)

    def publish_event(self, event: ProactiveEvent) -> int:
        """Enqueue an event and return count of matching subscriptions."""
        if not isinstance(event, ProactiveEvent):
            raise TypeError("event must be a ProactiveEvent instance.")

        with self._lock:
            self._event_queue.append(event)
            matching_count = sum(1 for s in self._subscriptions.values() if s.matches(event))
            return matching_count

    def poll_and_dispatch(
        self,
        goal_scheduler: MultiGoalScheduler | None = None,
        goal_engine: Any | None = None,
        max_events: int = 10,
        current_time: float | None = None,
    ) -> list[str]:
        """Process queued events, matching them against subscriptions and scheduling goals."""
        now = current_time if current_time is not None else time.time()
        dispatched_goal_ids: list[str] = []

        with self._lock:
            events_to_process: list[ProactiveEvent] = []
            while self._event_queue and len(events_to_process) < max_events:
                events_to_process.append(self._event_queue.popleft())

            for evt in events_to_process:
                # Find matching subscriptions
                matching_subs = [s for s in self._subscriptions.values() if s.matches(evt) and not s.is_on_cooldown(now)]

                for sub in matching_subs:
                    gid = sub.goal_id

                    # 1. Ingest observation into GoalEngine if available
                    if goal_engine and hasattr(goal_engine, "add_observation"):
                        try:
                            obs_meta = {
                                "topic": evt.topic,
                                "event_id": evt.event_id,
                                "trigger_id": sub.trigger_id,
                            }
                            goal_engine.add_observation(
                                goal_id=gid,
                                source=f"event:{evt.topic}",
                                data=evt.payload,
                                is_untrusted=evt.is_untrusted,
                                metadata=obs_meta,
                            )
                        except Exception as ex:
                            logger.warning("Failed to record event observation for goal '%s': %s", gid, ex)

                    # 2. Schedule goal in MultiGoalScheduler if available
                    if goal_scheduler and hasattr(goal_scheduler, "schedule_goal"):
                        try:
                            goal_scheduler.schedule_goal(
                                goal_id=gid,
                                current_time=now,
                                metadata={"triggered_by_event": evt.event_id, "topic": evt.topic},
                            )
                        except Exception as ex:
                            logger.warning("Failed to schedule goal '%s' on event: %s", gid, ex)

                    # 3. Update subscription cooldown
                    upd_sub = EventSubscription(
                        subscription_id=sub.subscription_id,
                        topic_pattern=sub.topic_pattern,
                        goal_id=sub.goal_id,
                        trigger_id=sub.trigger_id,
                        created_at=sub.created_at,
                        cooldown_seconds=sub.cooldown_seconds,
                        last_dispatched_at=now,
                        metadata=dict(sub.metadata),
                    )
                    self._subscriptions[sub.subscription_id] = upd_sub
                    dispatched_goal_ids.append(gid)

        return dispatched_goal_ids

    def process_cron_tick(
        self,
        cron_topic: str = "schedule.cron.tick",
        tick_payload: Any = None,
        current_time: float | None = None,
    ) -> int:
        """Publish a synthetic timer/cron tick event to trigger scheduled goals."""
        now = current_time if current_time is not None else time.time()
        evt = ProactiveEvent(
            event_type="cron",
            topic=cron_topic,
            payload=tick_payload or {"timestamp": now},
            source="system_scheduler",
            timestamp=now,
        )
        return self.publish_event(evt)

    def get_status(self) -> dict[str, Any]:
        """Return event queue and subscription metrics."""
        with self._lock:
            return {
                "queued_events_count": len(self._event_queue),
                "max_queue_size": self.max_queue_size,
                "active_subscriptions_count": len(self._subscriptions),
            }
