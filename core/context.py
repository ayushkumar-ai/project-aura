from uuid import UUID

from pydantic import BaseModel, Field

from core.models import AURARequest
from core.history import ConversationHistory


class AURAContext(BaseModel):
    """Execution context carried through the AURA pipeline."""

    request: AURARequest
    request_id: UUID
    state: dict[str, str] = Field(default_factory=dict)
    history: ConversationHistory = Field(default_factory=ConversationHistory)