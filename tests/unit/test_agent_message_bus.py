import pytest

from core.agent_message_bus import AgentMessageBus
from core.agent_message_types import AgentMessage, AgentMessageType


def test_agent_message_creation_and_metadata_sanitization():
    msg = AgentMessage(
        sender_role_id="Coder",
        recipient_role_id="Reviewer",
        message_type=AgentMessageType.CRITIQUE,
        content="Please review this function",
        payload={"diff": "+ def foo(): pass"},
        metadata={
            "is_authorized": "true",
            "is_admin": "true",
            "safe_tag": "v1.0",
        },
    )

    assert msg.sender_role_id == "coder"
    assert msg.recipient_role_id == "reviewer"
    assert msg.message_type == AgentMessageType.CRITIQUE
    assert msg.content == "Please review this function"
    assert msg.payload == {"diff": "+ def foo(): pass"}
    assert "is_authorized" not in msg.metadata
    assert "is_admin" not in msg.metadata
    assert msg.metadata["safe_tag"] == "v1.0"


def test_agent_message_bus_direct_send_and_receive():
    bus = AgentMessageBus()

    msg1 = bus.send_direct(
        sender="architect",
        recipient="coder",
        content="Implement task planner step",
        message_type=AgentMessageType.TASK_DELEGATION,
    )

    msg2 = bus.send_direct(
        sender="researcher",
        recipient="coder",
        content="Evidence found",
        message_type=AgentMessageType.TASK_RESULT,
    )

    assert bus.get_queue_depth("coder") == 2
    assert bus.get_queue_depth("architect") == 0

    # Peek without popping
    peeked = bus.peek("coder")
    assert len(peeked) == 2
    assert bus.get_queue_depth("coder") == 2

    # Filtered receive by type
    delegations = bus.receive("coder", message_type=AgentMessageType.TASK_DELEGATION)
    assert len(delegations) == 1
    assert delegations[0].message_id == msg1.message_id
    assert bus.get_queue_depth("coder") == 1

    # Receive remaining
    remaining = bus.receive("coder")
    assert len(remaining) == 1
    assert remaining[0].message_id == msg2.message_id
    assert bus.get_queue_depth("coder") == 0


def test_agent_message_bus_broadcast():
    bus = AgentMessageBus()

    # Pre-register coder and reviewer mailboxes by sending a direct message or checking
    bus.send_direct("coordinator", "coder", "Hello coder")
    bus.send_direct("coordinator", "reviewer", "Hello reviewer")

    # Clear their initial messages
    bus.receive("coder")
    bus.receive("reviewer")

    # Broadcast
    bus.broadcast(
        sender="coordinator",
        content="Team meeting at 10:00",
    )

    assert bus.get_queue_depth("coder") == 1
    assert bus.get_queue_depth("reviewer") == 1
    assert bus.get_queue_depth("coordinator") == 0  # Sender doesn't receive its own broadcast


def test_agent_message_bus_queue_overflow():
    bus = AgentMessageBus(max_queue_size=3)

    for i in range(5):
        bus.send_direct("architect", "coder", f"Message {i}")

    # Maximum 3 messages kept in mailbox (oldest dropped)
    assert bus.get_queue_depth("coder") == 3
    msgs = bus.receive("coder")
    assert [m.content for m in msgs] == ["Message 2", "Message 3", "Message 4"]


def test_agent_message_bus_payload_limit():
    bus = AgentMessageBus(max_payload_chars=50)

    with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
        bus.send_direct("architect", "coder", "X" * 60)


def test_agent_message_bus_pubsub_subscribers():
    bus = AgentMessageBus()
    received_msgs = []

    def on_message(msg: AgentMessage):
        received_msgs.append(msg)

    bus.subscribe("security_auditor", on_message)

    bus.send_direct("coder", "security_auditor", "Audit this PR")
    bus.send_direct("coder", "architect", "FYI")

    assert len(received_msgs) == 1
    assert received_msgs[0].content == "Audit this PR"

    # Unsubscribe
    assert bus.unsubscribe("security_auditor", on_message) is True
    bus.send_direct("coder", "security_auditor", "Second audit")
    assert len(received_msgs) == 1  # No new invocation
