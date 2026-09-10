import time
import pytest
from uuid import uuid4

from core.history import ConversationHistory
from core.provenance import TaintedValue, wrap_tainted
from core.session_types import (
    OperatorAction,
    OperatorActionType,
    OperatorResolution,
    SessionContext,
    SessionMetadata,
    SessionStatus,
    StreamEvent,
    StreamEventType,
    canonical_session_value,
    restore_session_value,
    sanitize_session_metadata,
    validate_session_id,
)


def test_session_id_validation():
    assert validate_session_id("sess-123") == "sess-123"
    assert validate_session_id("user_abc.1:2") == "user_abc.1:2"
    assert validate_session_id("default") == "default"

    with pytest.raises(ValueError, match="empty"):
        validate_session_id("")

    with pytest.raises(ValueError, match="empty"):
        validate_session_id("   ")

    with pytest.raises(ValueError, match="Invalid session_id"):
        validate_session_id("../secret")

    with pytest.raises(ValueError, match="Invalid session_id"):
        validate_session_id("session/123")

    with pytest.raises(ValueError, match="Invalid session_id"):
        validate_session_id("session\\123")

    with pytest.raises(ValueError, match="Invalid session_id"):
        validate_session_id("sess with spaces")

    with pytest.raises(TypeError):
        validate_session_id(123)  # type: ignore


def test_metadata_sanitization_removes_forbidden_keys():
    meta = {
        "client": "web",
        "approved": True,
        "is_authorized": True,
        "bypass_policy": True,
        "auto_approve": True,
        "role_override": "admin",
        "nested": {
            "safe": "yes",
            "permission": "root",
            "skip_approval": True,
        },
        "bad_callable": lambda: True,
    }
    sanitized = sanitize_session_metadata(meta)
    assert "approved" not in sanitized
    assert "is_authorized" not in sanitized
    assert "bypass_policy" not in sanitized
    assert "auto_approve" not in sanitized
    assert "role_override" not in sanitized
    assert "bad_callable" not in sanitized
    assert sanitized["client"] == "web"
    assert sanitized["nested"]["safe"] == "yes"
    assert "permission" not in sanitized["nested"]
    assert "skip_approval" not in sanitized["nested"]


def test_session_metadata_ttl_and_touch():
    now = time.time()
    meta = SessionMetadata(
        session_id="s1",
        user_id="user_1",
        created_at=now - 500,
        last_accessed_at=now - 500,
        ttl_seconds=300.0,
    )
    assert meta.is_expired(current_time=now) is True
    assert meta.version == 1

    meta.touch(current_time=now)
    assert meta.last_accessed_at == now
    assert meta.version == 2
    assert meta.is_expired(current_time=now) is False


def test_session_context_serialization_and_isolation():
    history = ConversationHistory()
    history.add_turn(
        user_input="hello",
        assistant_output="hi there",
        tool_name="calculator",
        tool_result="42",
    )
    meta = SessionMetadata(session_id="s123", user_id="u1")
    ctx = SessionContext(
        metadata=meta,
        history=history,
        active_goal_ids=["g1", "g2"],
        active_task_ids=["t1"],
        resource_limits={"max_tokens": 1000.0},
    )

    data = ctx.to_dict()
    assert data["metadata"]["session_id"] == "s123"
    assert len(data["history"]["turns"]) == 1
    assert data["history"]["turns"][0]["user_input"] == "hello"

    restored = SessionContext.from_dict(data)
    assert restored.session_id == "s123"
    assert restored.metadata.user_id == "u1"
    assert len(restored.history.turns) == 1
    assert restored.history.turns[0].tool_result == "42"
    assert restored.active_goal_ids == ["g1", "g2"]
    assert restored.active_task_ids == ["t1"]
    assert restored.resource_limits["max_tokens"] == 1000.0


def test_tainted_value_roundtrip_preservation():
    tainted = wrap_tainted(
        value="malicious string from web",
        is_untrusted=True,
        source_type="web_crawler",
        originating_step_id="step_99",
        source_urls=["https://example.com/data"],
        metadata={"domain": "example.com", "approved": True},  # 'approved' should be sanitized
    )

    canonical = canonical_session_value(tainted)
    assert canonical["__tainted__"] is True
    assert canonical["raw_value"] == "malicious string from web"
    assert canonical["is_untrusted"] is True
    assert canonical["source_type"] == "web_crawler"
    assert canonical["originating_step_id"] == "step_99"
    assert "approved" not in canonical["metadata"]

    restored = restore_session_value(canonical)
    assert isinstance(restored, TaintedValue)
    assert restored.raw_value == "malicious string from web"
    assert restored.is_untrusted is True
    assert restored.source_type == "web_crawler"
    assert restored.originating_step_id == "step_99"
    assert restored.source_urls == ("https://example.com/data",)


def test_stream_event_sse_and_ws():
    event = StreamEvent(
        session_id="s1",
        event_type=StreamEventType.STEP_STARTED,
        data={"step_name": "fetch_data", "step_num": 1},
        step_id="step_1",
        task_id="task_10",
    )
    sse = event.to_sse()
    assert f"id: {event.event_id}" in sse
    assert "event: step_started" in sse
    assert '"step_name": "fetch_data"' in sse
    assert sse.endswith("\n\n")

    ws = event.to_ws_message()
    assert f'"event_type": "step_started"' in ws
    assert f'"session_id": "s1"' in ws

    d = event.to_dict()
    restored = StreamEvent.from_dict(d)
    assert restored.event_id == event.event_id
    assert restored.event_type == StreamEventType.STEP_STARTED
    assert restored.data["step_num"] == 1


def test_operator_action_and_resolution():
    action = OperatorAction(
        session_id="s1",
        request_id="req_999",
        action_type=OperatorActionType.APPROVE,
        operator_id="admin_user",
        decision_rationale="Approved after manual audit",
        metadata={"client_ip": "127.0.0.1", "bypass_policy": True},
    )
    assert "bypass_policy" not in action.metadata
    assert action.action_type == OperatorActionType.APPROVE

    d = action.to_dict()
    restored = OperatorAction.from_dict(d)
    assert restored.action_id == action.action_id
    assert restored.request_id == "req_999"
    assert restored.decision_rationale == "Approved after manual audit"

    res = OperatorResolution(
        action_id=action.action_id,
        request_id="req_999",
        success=True,
        status="approved",
        message="Action successfully approved and resumed",
    )
    res_dict = res.to_dict()
    assert res_dict["success"] is True
    assert res_dict["status"] == "approved"
