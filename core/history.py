from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Represents one turn in an AURA conversation."""

    user_input: str
    assistant_output: str
    tool_name: str | None = None
    tool_result: str | None = None


class ConversationHistory(BaseModel):
    """Represents the conversation history for AURA."""

    turns: list[ConversationTurn] = Field(default_factory=list)

    def add_turn(
        self,
        user_input: str,
        assistant_output: str,
        tool_name: str | None = None,
        tool_result: str | None = None,
    ) -> None:
        """Add a completed conversation turn."""
        self.turns.append(
            ConversationTurn(
                user_input=user_input,
                assistant_output=assistant_output,
                tool_name=tool_name,
                tool_result=tool_result,
            )
        )
