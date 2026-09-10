import pytest
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.goal import GoalStatus
from core.session_types import SessionStatus, StreamEventType
from providers.fake_model import FakeModelProvider


def test_agentic_runtime_session_management():
    runtime = AgenticRuntime(model=FakeModelProvider())
    ctx = runtime.create_session("sess_rt_1", user_id="user_rt")
    assert ctx.session_id == "sess_rt_1"
    assert ctx.status == SessionStatus.ACTIVE

    fetched = runtime.get_session("sess_rt_1")
    assert fetched is not None
    assert fetched.session_id == "sess_rt_1"

    assert runtime.close_session("sess_rt_1") is True
    assert runtime.get_session("sess_rt_1").status == SessionStatus.COMPLETED


def test_agentic_runtime_submit_session_goal():
    runtime = AgenticRuntime(model=FakeModelProvider())
    sub_id, q = runtime.subscribe_stream(session_id="sess_goal_1")

    goal = runtime.submit_session_goal(
        title="Deploy multi-session service",
        session_id="sess_goal_1",
    )
    assert goal.title == "Deploy multi-session service"

    # Verify session binding
    ctx = runtime.get_session("sess_goal_1")
    assert goal.goal_id in ctx.active_goal_ids

    # Verify event stream
    assert q.qsize() == 1
    ev = q.get_nowait()
    assert ev.event_type == StreamEventType.GOAL_UPDATED
    assert ev.data["goal_id"] == goal.goal_id


def test_agentic_runtime_send_message_stream():
    runtime = AgenticRuntime(model=FakeModelProvider())
    events = list(runtime.send_message_stream("What is the system status?", session_id="sess_stream_1"))

    assert len(events) >= 3
    event_types = [e.event_type for e in events]
    assert StreamEventType.STEP_STARTED in event_types
    assert StreamEventType.TOKEN_CHUNK in event_types
    assert StreamEventType.STEP_COMPLETED in event_types

    # Verify session history updated
    ctx = runtime.get_session("sess_stream_1")
    assert len(ctx.history.turns) == 1
    assert ctx.history.turns[0].user_input == "What is the system status?"
