from uuid import UUID

from pydantic import BaseModel, Field

from core.models import AURARequest


class AURAContext(BaseModel):
    """Execution context carried through the AURA pipeline."""

    request: AURARequest
    request_id: UUID
    state: dict[str, str] = Field(default_factory=dict)