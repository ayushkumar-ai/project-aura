import pytest
from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.history import ConversationHistory
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.session_types import SessionStatus, StreamEventType
from providers.fake_model import FakeModelProvider


def test_aura_facade_session_management():
    model = FakeModelProvider()
    runtime = AgenticRuntime(model=model)
    policy = Policy()
    orch = Orchestrator(model=model, policy=policy, history=ConversationHistory())
    aura = AURA(orchestrator=orch, agentic_runtime=runtime)

    ctx = aura.create_session("aura_sess_1", user_id="user_aura")
    assert ctx.session_id == "aura_sess_1"

    status = aura.get_session_status("aura_sess_1")
    assert status["status"] == "active"
    assert status["user_id"] == "user_aura"

    sessions = aura.list_sessions(user_id="user_aura")
    assert len(sessions) == 1

    assert aura.close_session("aura_sess_1") is True
    assert aura.get_session_status("aura_sess_1")["status"] == "completed"


def test_aura_facade_send_message_stream():
    model = FakeModelProvider()
    runtime = AgenticRuntime(model=model)
    policy = Policy()
    orch = Orchestrator(model=model, policy=policy, history=ConversationHistory())
    aura = AURA(orchestrator=orch, agentic_runtime=runtime)

    sub_id, q = aura.subscribe_events(session_id="aura_stream_sess")

    events = list(aura.send_message_stream("Summarize project state", session_id="aura_stream_sess"))
    assert len(events) >= 3

    # Verify queue also received events
    assert q.qsize() >= 3

    status = aura.get_session_status("aura_stream_sess")
    assert status["turn_count"] == 1


def test_aura_facade_submit_session_goal():
    model = FakeModelProvider()
    runtime = AgenticRuntime(model=model)
    policy = Policy()
    orch = Orchestrator(model=model, policy=policy, history=ConversationHistory())
    aura = AURA(orchestrator=orch, agentic_runtime=runtime)

    goal = aura.submit_session_goal("Implement real-time dashboards", session_id="dash_sess")
    assert goal.title == "Implement real-time dashboards"

    status = aura.get_session_status("dash_sess")
    assert goal.goal_id in status["active_goals"]
