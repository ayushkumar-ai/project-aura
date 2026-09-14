from uuid import UUID
from typing import Any
from pydantic import BaseModel, Field

from core.models import AURARequest
from core.history import ConversationHistory


class AURAContext(BaseModel):
    """Execution context carried through the AURA pipeline."""

    model_config = {"arbitrary_types_allowed": True}

    request: AURARequest
    request_id: UUID
    state: dict[str, str] = Field(default_factory=dict)
    history: ConversationHistory = Field(default_factory=ConversationHistory)
    identity: Any | None = None
    user_id: str = "default"
