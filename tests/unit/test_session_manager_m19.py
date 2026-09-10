import time
import threading
import pytest
from uuid import uuid4

from core.session_manager import SessionManager
from core.session_store import InMemorySessionStore
from core.session_types import SessionStatus


def test_create_and_get_session():
    mgr = SessionManager(default_ttl_seconds=300.0)
    ctx = mgr.create_session(session_id="sess_100", user_id="charlie")
    assert ctx.session_id == "sess_100"
    assert ctx.metadata.user_id == "charlie"
    assert ctx.status == SessionStatus.ACTIVE

    fetched = mgr.get_session("sess_100")
    assert fetched is not None
    assert fetched.session_id == "sess_100"

    # Duplicate create raises ValueError
    with pytest.raises(ValueError, match="already exists"):
        mgr.create_session(session_id="sess_100")


def test_get_or_create_session():
    mgr = SessionManager()
    ctx1 = mgr.get_or_create_session("sess_auto", user_id="david")
    assert ctx1.session_id == "sess_auto"

    ctx2 = mgr.get_or_create_session("sess_auto")
    assert ctx2.session_id == "sess_auto"
    assert ctx2.metadata.user_id == "david"


def test_session_isolation():
    mgr = SessionManager()
    mgr.add_turn("sess_A", user_input="Hello A", assistant_output="Echo A")
    mgr.add_turn("sess_B", user_input="Hello B", assistant_output="Echo B")

    hist_A = mgr.get_history("sess_A")
    hist_B = mgr.get_history("sess_B")

    assert len(hist_A.turns) == 1
    assert hist_A.turns[0].user_input == "Hello A"
    assert len(hist_B.turns) == 1
    assert hist_B.turns[0].user_input == "Hello B"


def test_pause_resume_close_lifecycle():
    mgr = SessionManager()
    mgr.create_session("sess_lc")

    assert mgr.pause_session("sess_lc") is True
    assert mgr.get_session("sess_lc").status == SessionStatus.PAUSED

    # Cannot pause already paused
    assert mgr.pause_session("sess_lc") is False

    assert mgr.resume_session("sess_lc") is True
    assert mgr.get_session("sess_lc").status == SessionStatus.ACTIVE

    assert mgr.close_session("sess_lc", reason="completed_all_goals") is True
    assert mgr.get_session("sess_lc").status == SessionStatus.COMPLETED


def test_goal_and_task_bindings():
    mgr = SessionManager()
    mgr.create_session("sess_bindings")

    assert mgr.bind_goal("sess_bindings", "goal_1") is True
    assert mgr.bind_goal("sess_bindings", "goal_2") is True
    assert mgr.bind_task("sess_bindings", "task_1") is True

    ctx = mgr.get_session("sess_bindings")
    assert ctx.active_goal_ids == ["goal_1", "goal_2"]
    assert ctx.active_task_ids == ["task_1"]

    assert mgr.unbind_goal("sess_bindings", "goal_1") is True
    assert mgr.unbind_task("sess_bindings", "task_1") is True

    ctx_updated = mgr.get_session("sess_bindings")
    assert ctx_updated.active_goal_ids == ["goal_2"]
    assert ctx_updated.active_task_ids == []


def test_history_bounding():
    mgr = SessionManager(max_history_turns=3)
    mgr.create_session("sess_bounded")

    for i in range(5):
        mgr.add_turn("sess_bounded", user_input=f"In {i}", assistant_output=f"Out {i}")

    hist = mgr.get_history("sess_bounded")
    assert len(hist.turns) == 3
    assert hist.turns[0].user_input == "In 2"
    assert hist.turns[2].user_input == "In 4"


def test_max_active_sessions_capacity():
    mgr = SessionManager(max_active_sessions=2, default_ttl_seconds=10.0)
    mgr.create_session("s1")
    mgr.create_session("s2")

    with pytest.raises(RuntimeError, match="Active session capacity reached"):
        mgr.create_session("s3")


def test_concurrent_session_manager_mutations():
    mgr = SessionManager(max_active_sessions=100)
    errors = []

    def worker(worker_id: int):
        try:
            sid = f"concurrent_sess_{worker_id}"
            mgr.create_session(sid, user_id=f"user_{worker_id}")
            for t in range(5):
                mgr.add_turn(sid, user_input=f"q_{t}", assistant_output=f"a_{t}")
                mgr.bind_goal(sid, f"g_{t}")
            hist = mgr.get_history(sid)
            if len(hist.turns) != 5:
                errors.append(f"Turn count mismatch in worker {worker_id}: {len(hist.turns)}")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(mgr.list_sessions()) == 10
