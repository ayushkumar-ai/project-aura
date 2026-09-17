"""M54 — Administrative Replay Service for Dead-Letter Events."""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_webhook import BaseWebhookRepository
import logging
import uuid
from datetime import datetime

from core.webhooks.types import DeadLetterReplay, EventDelivery, DeliveryStatus

logger = logging.getLogger("aura.webhooks.replay")


class AdministrativeReplayService:
    """Manages administrative replays of dead-lettered events while preserving historical immutability."""

    def __init__(self, webhook_repo: BaseWebhookRepository) -> None:
        self.webhook_repo = webhook_repo

    def replay_dead_letter(
        self,
        dead_letter_id: str,
        user_id: str,
        replayed_by: str,
        reason: str = "",
    ) -> EventDelivery:
        """Create a brand new delivery record from an existing dead-letter event.
        
        The historical dead_letter_events record remains 100% byte-identical and immutable.
        Replay audit records are written to dead_letter_replays.
        """
        dl_event = self.webhook_repo.get_dead_letter_event(dead_letter_id, user_id=user_id)
        if not dl_event:
            raise ValueError(f"Dead letter event '{dead_letter_id}' not found or unauthorized")

        new_delivery_id = f"del_{uuid.uuid4()}"
        replay_id = f"dlr_{uuid.uuid4()}"
        now = datetime.utcnow()

        # Build new delivery record
        new_delivery = EventDelivery(
            id=new_delivery_id,
            subscription_id=dl_event.inbound_event_id or dl_event.delivery_id or "replay",
            user_id=user_id,
            event_id=dl_event.inbound_event_id or dl_event.delivery_id or f"evt_{uuid.uuid4()}",
            target_url="https://replay.target.local",
            payload=dl_event.payload,
            status=DeliveryStatus.PENDING,
            attempts=0,
            max_attempts=5,
            next_attempt_at=now,
            causation_id=dl_event.id,
            replayed_from_dead_letter_id=dead_letter_id,
            created_at=now,
        )

        replay_record = DeadLetterReplay(
            id=replay_id,
            dead_letter_id=dead_letter_id,
            new_delivery_id=new_delivery_id,
            user_id=user_id,
            replayed_by=replayed_by,
            replayed_at=now,
            reason=reason,
        )

        _, created_deliv = self.webhook_repo.create_dead_letter_replay(replay_record, new_delivery)
        logger.info(f"Replayed dead-letter event '{dead_letter_id}' into new delivery '{new_delivery_id}'")
        return created_deliv

DeadLetterReplayService = AdministrativeReplayService
