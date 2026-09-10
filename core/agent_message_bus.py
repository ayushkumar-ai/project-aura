import logging
import threading
from collections.abc import Callable
from typing import Any

from app.config import settings
from core.agent_message_types import AgentMessage, AgentMessageType

logger = logging.getLogger("aura.agent_message_bus")


class AgentMessageBus:
    """Thread-safe asynchronous message bus for peer-to-peer and broadcast agent communication."""

    def __init__(
        self,
        max_queue_size: int | None = None,
        max_payload_chars: int | None = None,
        max_history_size: int | None = None,
    ):
        self.max_queue_size: int = (
            int(max_queue_size)
            if max_queue_size is not None
            else int(getattr(settings, "aura_message_bus_max_queue_size", 1000))
        )
        self.max_payload_chars: int = (
            int(max_payload_chars)
            if max_payload_chars is not None
            else int(getattr(settings, "aura_max_message_payload_chars", 100000))
        )
        self.max_history_size: int = (
            int(max_history_size)
            if max_history_size is not None
            else int(getattr(settings, "aura_message_bus_max_history_size", 5000))
        )

        self._lock = threading.RLock()
        self._mailboxes: dict[str, list[AgentMessage]] = {}  # recipient_role_id -> list of messages
        self._history: list[AgentMessage] = []
        self._subscribers: dict[str, list[Callable[[AgentMessage], None]]] = {}  # role_id or "*" -> callbacks

    def publish(self, message: AgentMessage) -> None:
        """Validate and publish a message onto the bus."""
        if not isinstance(message, AgentMessage):
            raise TypeError("message must be an instance of AgentMessage.")

        # Enforce payload bounds
        if len(message.content) > self.max_payload_chars:
            raise ValueError(
                f"Message content length ({len(message.content)}) exceeds maximum allowed limit ({self.max_payload_chars})."
            )

        callbacks_to_invoke: list[Callable[[AgentMessage], None]] = []

        with self._lock:
            # 1. Store in historical log with bound
            self._history.append(message)
            if len(self._history) > self.max_history_size:
                self._history.pop(0)

            # 2. Route to mailboxes
            recip = message.recipient_role_id.strip().lower()
            if recip == "broadcast":
                # Add to all existing mailboxes and a dedicated broadcast queue
                for r_id in self._mailboxes:
                    if r_id != message.sender_role_id:
                        box = self._mailboxes[r_id]
                        if len(box) < self.max_queue_size:
                            box.append(message)
                        else:
                            logger.warning("Mailbox overflow for role '%s'; dropping oldest message.", r_id)
                            box.pop(0)
                            box.append(message)
            else:
                if recip not in self._mailboxes:
                    self._mailboxes[recip] = []
                box = self._mailboxes[recip]
                if len(box) >= self.max_queue_size:
                    logger.warning("Mailbox overflow for role '%s'; dropping oldest message.", recip)
                    box.pop(0)
                box.append(message)

            # 3. Collect active subscribers
            if recip in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers[recip])
            if "broadcast" in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers["broadcast"])
            if "*" in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers["*"])

        # Execute callbacks outside lock
        for cb in callbacks_to_invoke:
            try:
                cb(message)
            except Exception as cb_err:
                logger.error("Error executing subscriber callback on message '%s': %s", message.message_id, cb_err)

    post_message = publish

    def send_direct(
        self,
        sender: str,
        recipient: str,
        content: str,
        message_type: AgentMessageType | str = AgentMessageType.TASK_DELEGATION,
        payload: dict[str, Any] | None = None,
        session_id: str = "default",
        team_id: str = "default_team",
        is_untrusted: bool = True,
        parent_message_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> AgentMessage:
        """Construct and publish a direct peer-to-peer message."""
        msg = AgentMessage(
            session_id=session_id,
            team_id=team_id,
            sender_role_id=sender,
            recipient_role_id=recipient,
            message_type=AgentMessageType(message_type) if isinstance(message_type, str) else message_type,
            content=content,
            payload=payload or {},
            is_untrusted=is_untrusted,
            parent_message_id=parent_message_id,
            metadata=metadata or {},
        )
        self.publish(msg)
        return msg

    def broadcast(
        self,
        sender: str,
        content: str,
        message_type: AgentMessageType | str = AgentMessageType.BROADCAST,
        payload: dict[str, Any] | None = None,
        session_id: str = "default",
        team_id: str = "default_team",
        is_untrusted: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> AgentMessage:
        """Construct and broadcast a message to all agent mailboxes."""
        msg = AgentMessage(
            session_id=session_id,
            team_id=team_id,
            sender_role_id=sender,
            recipient_role_id="broadcast",
            message_type=AgentMessageType(message_type) if isinstance(message_type, str) else message_type,
            content=content,
            payload=payload or {},
            is_untrusted=is_untrusted,
            metadata=metadata or {},
        )
        self.publish(msg)
        return msg

    def receive(
        self,
        role_id: str,
        limit: int | None = None,
        message_type: AgentMessageType | str | None = None,
    ) -> list[AgentMessage]:
        """Fetch and remove pending messages from an agent's mailbox."""
        if not isinstance(role_id, str) or not role_id.strip():
            raise ValueError("role_id must be a non-empty string.")
        norm_id = role_id.strip().lower()

        target_type = AgentMessageType(message_type) if isinstance(message_type, str) else message_type

        with self._lock:
            if norm_id not in self._mailboxes:
                return []

            box = self._mailboxes[norm_id]
            if not box:
                return []

            if target_type is None:
                if limit is None or limit >= len(box):
                    msgs = list(box)
                    self._mailboxes[norm_id] = []
                    return msgs
                else:
                    msgs = box[:limit]
                    self._mailboxes[norm_id] = box[limit:]
                    return msgs
            else:
                matched: list[AgentMessage] = []
                remaining: list[AgentMessage] = []
                for m in box:
                    if (limit is None or len(matched) < limit) and m.message_type == target_type:
                        matched.append(m)
                    else:
                        remaining.append(m)
                self._mailboxes[norm_id] = remaining
                return matched

    def peek(self, role_id: str, limit: int | None = None) -> list[AgentMessage]:
        """Inspect pending messages for an agent without removing them."""
        norm_id = role_id.strip().lower()
        with self._lock:
            box = self._mailboxes.get(norm_id, [])
            if limit is not None:
                return list(box[:limit])
            return list(box)

    def subscribe(self, role_id: str, callback: Callable[[AgentMessage], None]) -> None:
        """Subscribe a callback to messages addressed to a specific role or '*' for all."""
        norm_id = role_id.strip().lower()
        with self._lock:
            if norm_id not in self._subscribers:
                self._subscribers[norm_id] = []
            if callback not in self._subscribers[norm_id]:
                self._subscribers[norm_id].append(callback)

    def unsubscribe(self, role_id: str, callback: Callable[[AgentMessage], None]) -> bool:
        """Unsubscribe a previously registered callback."""
        norm_id = role_id.strip().lower()
        with self._lock:
            if norm_id in self._subscribers and callback in self._subscribers[norm_id]:
                self._subscribers[norm_id].remove(callback)
                return True
            return False

    def get_queue_depth(self, role_id: str) -> int:
        """Get the count of unread messages in an agent's mailbox."""
        norm_id = role_id.strip().lower()
        with self._lock:
            return len(self._mailboxes.get(norm_id, []))

    def get_history(
        self,
        team_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentMessage]:
        """Retrieve recent message history filtered by team or session."""
        with self._lock:
            filtered = self._history
            if team_id is not None:
                norm_t = team_id.strip().lower()
                filtered = [m for m in filtered if m.team_id.strip().lower() == norm_t]
            if session_id is not None:
                norm_s = session_id.strip()
                filtered = [m for m in filtered if m.session_id.strip() == norm_s]
            return list(filtered[-limit:])

    def clear(self, team_id: str | None = None) -> None:
        """Clear mailboxes and message history."""
        with self._lock:
            if team_id is None:
                self._mailboxes.clear()
                self._history.clear()
            else:
                norm_t = team_id.strip().lower()
                for r_id, box in list(self._mailboxes.items()):
                    self._mailboxes[r_id] = [m for m in box if m.team_id.strip().lower() != norm_t]
                self._history = [m for m in self._history if m.team_id.strip().lower() != norm_t]
