import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("aura.agent_message_types")

FORBIDDEN_MESSAGE_METADATA_KEYS = frozenset({
    "is_authorized",
    "is_admin",
    "approved",
    "bypass_policy",
    "sudo",
})


class AgentMessageType(str, Enum):
    """Standard taxonomy of inter-agent messages within the AURA multi-agent mesh."""

    TASK_DELEGATION = "task_delegation"
    TASK_RESULT = "task_result"
    PEER_FEEDBACK = "peer_feedback"
    CRITIQUE = "critique"
    VOTE = "vote"
    CLARIFICATION = "clarification"
    BROADCAST = "broadcast"
    HANDOFF = "handoff"
    ERROR = "error"


@dataclass
class AgentMessage:
    """Represents an atomic, structured message transmitted across the agent message bus."""

    message_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = "default"
    team_id: str = "default_team"
    sender_role_id: str = ""
    recipient_role_id: str = ""  # Role ID or "broadcast"
    message_type: AgentMessageType = AgentMessageType.TASK_DELEGATION
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    is_untrusted: bool = True
    parent_message_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            self.message_id = str(uuid4())
        self.message_id = self.message_id.strip()

        if not isinstance(self.sender_role_id, str) or not self.sender_role_id.strip():
            raise ValueError("sender_role_id must be a non-empty string.")
        self.sender_role_id = self.sender_role_id.strip().lower()

        if not isinstance(self.recipient_role_id, str) or not self.recipient_role_id.strip():
            raise ValueError("recipient_role_id must be a non-empty string.")
        self.recipient_role_id = self.recipient_role_id.strip().lower()

        if isinstance(self.message_type, str):
            self.message_type = AgentMessageType(self.message_type)
        elif not isinstance(self.message_type, AgentMessageType):
            raise TypeError("message_type must be an instance of AgentMessageType.")

        if not isinstance(self.content, str):
            self.content = str(self.content)

        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dictionary.")

        if not isinstance(self.is_untrusted, bool):
            self.is_untrusted = True

        # Sanitize metadata
        cleaned_meta: dict[str, str] = {}
        if isinstance(self.metadata, dict):
            for k, v in self.metadata.items():
                k_str = str(k).strip().lower()
                if k_str in FORBIDDEN_MESSAGE_METADATA_KEYS:
                    logger.warning(
                        "Security warning: Filtered forbidden key '%s' from message '%s' metadata.",
                        k,
                        self.message_id,
                    )
                    continue
                cleaned_meta[str(k).strip()] = str(v)
        self.metadata = cleaned_meta

    def to_dict(self) -> dict[str, Any]:
        """Serialize message to dictionary."""
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "team_id": self.team_id,
            "sender_role_id": self.sender_role_id,
            "recipient_role_id": self.recipient_role_id,
            "message_type": self.message_type.value,
            "content": self.content,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "is_untrusted": self.is_untrusted,
            "parent_message_id": self.parent_message_id,
            "metadata": dict(self.metadata),
        }
