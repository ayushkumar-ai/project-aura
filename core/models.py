from uuid import UUID, uuid4
from typing import Any
from pydantic import BaseModel, Field


class AURARequest(BaseModel):
    """Represents a request entering the AURA system."""

    model_config = {"arbitrary_types_allowed": True}

    request_id: UUID = Field(default_factory=uuid4)
    user_input: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    identity: Any | None = None
    user_id: str | None = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.user_id is None and self.identity is not None:
            uid = getattr(self.identity, "user_id", None)
            if uid is not None:
                object.__setattr__(self, "user_id", str(uid))


class AURAResponse(BaseModel):
    """Represents a response produced by the AURA system."""

    model_config = {"arbitrary_types_allowed": True}

    request_id: UUID
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

