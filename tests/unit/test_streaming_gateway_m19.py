import queue
import threading
import time
import pytest

from core.session_types import StreamEvent, StreamEventType
from core.streaming_gateway import StreamingGateway


def test_subscribe_and_publish():
    gw = StreamingGateway()
    assert gw.active_subscriber_count == 0

    sub_id, q = gw.subscribe(session_id="s1")
    assert gw.active_subscriber_count == 1

    ev1 = gw.create_and_publish(
        session_id="s1",
        event_type=StreamEventType.STEP_STARTED,
        data={"step": 1},
    )
    assert q.qsize() == 1
    received = q.get_nowait()
    assert received.event_id == ev1.event_id
    assert received.event_type == StreamEventType.STEP_STARTED

    assert gw.unsubscribe(sub_id) is True
    assert gw.active_subscriber_count == 0


def test_filtering_by_session_and_event_type():
    gw = StreamingGateway()

    # Sub 1: only session s1, only TOKEN_CHUNK
    sub1, q1 = gw.subscribe(session_id="s1", event_types={StreamEventType.TOKEN_CHUNK})
    # Sub 2: only session s2
    sub2, q2 = gw.subscribe(session_id="s2")
    # Sub 3: all sessions, all events
    sub3, q3 = gw.subscribe()

    # Event on s1, STEP_STARTED -> should go to sub3 only
    gw.create_and_publish("s1", StreamEventType.STEP_STARTED, {"data": "start"})
    assert q1.qsize() == 0
    assert q2.qsize() == 0
    assert q3.qsize() == 1

    # Event on s1, TOKEN_CHUNK -> should go to sub1 and sub3
    gw.create_and_publish("s1", StreamEventType.TOKEN_CHUNK, {"chunk": "hello"})
    assert q1.qsize() == 1
    assert q2.qsize() == 0
    assert q3.qsize() == 2

    # Event on s2, STEP_COMPLETED -> should go to sub2 and sub3
    gw.create_and_publish("s2", StreamEventType.STEP_COMPLETED, {"result": "ok"})
    assert q1.qsize() == 1
    assert q2.qsize() == 1
    assert q3.qsize() == 3


def test_queue_overflow_non_blocking():
    # Gateway with small queue
    gw = StreamingGateway(max_queue_size=2)
    sub_id, q = gw.subscribe(session_id="s1")

    # Publish 5 events rapidly without reading
    for i in range(5):
        gw.create_and_publish("s1", StreamEventType.STEP_PROGRESS, {"i": i})

    # Queue should be at maxsize (2) and not crashed
    assert q.qsize() == 2
    # Latest events should be preserved
    item1 = q.get_nowait()
    item2 = q.get_nowait()
    assert item1.data["i"] == 3
    assert item2.data["i"] == 4


def test_last_event_id_replay():
    gw = StreamingGateway(replay_buffer_size=10)

    ev1 = gw.create_and_publish("s1", StreamEventType.STEP_STARTED, {"i": 1})
    ev2 = gw.create_and_publish("s1", StreamEventType.STEP_PROGRESS, {"i": 2})
    ev3 = gw.create_and_publish("s1", StreamEventType.STEP_COMPLETED, {"i": 3})

    # New subscriber connects with last_event_id = ev1.event_id
    sub_id, q = gw.subscribe(session_id="s1", last_event_id=ev1.event_id)

    # Should have replayed ev2 and ev3
    assert q.qsize() == 2
    rec1 = q.get_nowait()
    rec2 = q.get_nowait()
    assert rec1.event_id == ev2.event_id
    assert rec2.event_id == ev3.event_id


def test_concurrent_publish_and_subscribe():
    gw = StreamingGateway()
    errors = []

    def subscriber_worker(worker_id: int):
        try:
            sub_id, q = gw.subscribe(session_id=f"sess_{worker_id}")
            time.sleep(0.01)
            gw.create_and_publish(f"sess_{worker_id}", StreamEventType.TOKEN_CHUNK, {"worker": worker_id})
            received = q.get(timeout=2.0)
            if received.data["worker"] != worker_id:
                errors.append(f"Worker {worker_id} received wrong data")
            gw.unsubscribe(sub_id)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=subscriber_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert gw.active_subscriber_count == 0
