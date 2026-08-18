from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AURARequest(BaseModel):
    """Represents a request entering the AURA system."""

    request_id: UUID = Field(default_factory=uuid4)
    user_input: str
    metadata: dict[str, str] = Field(default_factory=dict)

class AURAResponse(BaseModel):
    """Represents a response produced by the AURA system."""

    request_id: UUID
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)