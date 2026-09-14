from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Represents one turn in an AURA conversation."""

    user_input: str
    assistant_output: str
    tool_name: str | None = None
    tool_result: str | None = None
    user_id: str = "default"


class ConversationHistory(BaseModel):
    """Represents the conversation history for AURA with user-scoped isolation."""

    turns: list[ConversationTurn] = Field(default_factory=list)

    def add_turn(
        self,
        user_input: str,
        assistant_output: str,
        tool_name: str | None = None,
        tool_result: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Add a completed conversation turn with user isolation."""
        self.turns.append(
            ConversationTurn(
                user_input=user_input,
                assistant_output=assistant_output,
                tool_name=tool_name,
                tool_result=tool_result,
                user_id=user_id if (user_id and str(user_id).strip()) else "default",
            )
        )

    def get_turns(self, user_id: str | None = None) -> list[ConversationTurn]:
        """Retrieve conversation turns, optionally isolated to a specific user_id."""
        if user_id is None or not str(user_id).strip():
            return list(self.turns)
        target_uid = str(user_id).strip()
        return [t for t in self.turns if t.user_id == target_uid]

    def clear(self, user_id: str | None = None) -> None:
        """Clear turns for a specific user_id or all turns if user_id is None."""
        if user_id is None:
            self.turns.clear()
        else:
            target_uid = str(user_id).strip()
            self.turns = [t for t in self.turns if t.user_id != target_uid]
