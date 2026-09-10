import pytest
from core.event_dispatcher import ProactiveEventDispatcher
from core.scheduling_types import ProactiveEvent
from core.provenance import wrap_tainted


class FakeGoalEngine:
    def __init__(self):
        self.observations = []

    def add_observation(self, goal_id, source, data, is_untrusted=False, metadata=None):
        self.observations.append({
            "goal_id": goal_id,
            "source": source,
            "data": data,
            "is_untrusted": is_untrusted,
            "metadata": metadata or {},
        })


class FakeScheduler:
    def __init__(self):
        self.scheduled = []

    def schedule_goal(self, goal_id, current_time=None, metadata=None):
        self.scheduled.append(goal_id)


def test_event_subscription_and_publishing():
    disp = ProactiveEventDispatcher()
    sub = disp.subscribe(topic_pattern="orders.*", goal_id="goal_orders")

    evt = ProactiveEvent(topic="orders.created", payload={"order_id": 101})
    match_count = disp.publish_event(evt)
    assert match_count == 1
    assert disp.get_status()["queued_events_count"] == 1


def test_event_poll_and_dispatch():
    disp = ProactiveEventDispatcher()
    engine = FakeGoalEngine()
    sched = FakeScheduler()

    disp.subscribe(topic_pattern="system.cpu.high", goal_id="goal_alert")
    evt = ProactiveEvent(topic="system.cpu.high", payload={"load": 98})
    disp.publish_event(evt)

    dispatched = disp.poll_and_dispatch(goal_scheduler=sched, goal_engine=engine)
    assert dispatched == ["goal_alert"]
    assert len(engine.observations) == 1
    assert engine.observations[0]["goal_id"] == "goal_alert"
    assert engine.observations[0]["data"] == {"load": 98}
    assert sched.scheduled == ["goal_alert"]


def test_event_cooldown_debounce():
    disp = ProactiveEventDispatcher()
    sched = FakeScheduler()
    engine = FakeGoalEngine()

    disp.subscribe(topic_pattern="ticker.*", goal_id="goal_tick", cooldown_seconds=60.0)

    # Publish 2 events
    disp.publish_event(ProactiveEvent(topic="ticker.price", payload=100))
    disp.publish_event(ProactiveEvent(topic="ticker.price", payload=101))

    # First event dispatches; second is suppressed by cooldown
    dispatched = disp.poll_and_dispatch(goal_scheduler=sched, goal_engine=engine, current_time=100.0)
    assert dispatched == ["goal_tick"]
    assert len(engine.observations) == 1


def test_event_taint_propagation():
    disp = ProactiveEventDispatcher()
    engine = FakeGoalEngine()
    sched = FakeScheduler()

    disp.subscribe(topic_pattern="webhook.data", goal_id="goal_sink")
    tainted = wrap_tainted("injected_html", is_untrusted=True, source_type="webhook")
    evt = ProactiveEvent(topic="webhook.data", payload=tainted)

    disp.publish_event(evt)
    disp.poll_and_dispatch(goal_scheduler=sched, goal_engine=engine)

    assert len(engine.observations) == 1
    assert engine.observations[0]["is_untrusted"] is True


def test_cron_tick_synthesis():
    disp = ProactiveEventDispatcher()
    disp.subscribe(topic_pattern="schedule.cron.tick", goal_id="goal_cron")

    count = disp.process_cron_tick()
    assert count == 1
    assert disp.get_status()["queued_events_count"] == 1


def test_unsubscribe():
    disp = ProactiveEventDispatcher()
    sub = disp.subscribe(topic_pattern="test.*", goal_id="goal_1")
    assert disp.get_status()["active_subscriptions_count"] == 1

    assert disp.unsubscribe(sub.subscription_id) is True
    assert disp.get_status()["active_subscriptions_count"] == 0
