from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    """Result produced when evaluating an AURA execution."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    details: dict[str, str] = Field(default_factory=dict)