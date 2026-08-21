from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Represents one turn in an AURA conversation."""

    user_input: str
    assistant_output: str


class ConversationHistory(BaseModel):
    """Represents the conversation history for AURA."""
    turns: list[ConversationTurn] = Field(default_factory=list)
    def add_turn(self, user_input: str, assistant_output: str) -> None:
        """Add a completed conversation turn."""
        self.turns.append(
            ConversationTurn(
                user_input=user_input,
                assistant_output=assistant_output,
            )
        )