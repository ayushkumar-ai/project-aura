"""M32 — Context & Personalization Engine Types.

Defines context priorities, item representations, token/character budgeting,
and assembled personalized context bundles.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any


class ContextPriority(IntEnum):
    CRITICAL = 1
    USER_INSTRUCTION = 2
    TASK_STATE = 3
    USER_PREFERENCES = 4
    RETRIEVED_FACTS = 5
    CONVERSATION_HISTORY = 6
    EXPERIENCE_HEURISTICS = 7
    BACKGROUND_KNOWLEDGE = 8


@dataclass
class ContextItem:
    item_id: str
    priority: ContextPriority
    source: str
    title: str
    content: str
    char_length: int = 0
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.char_length:
            self.char_length = len(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "priority": int(self.priority),
            "priority_name": self.priority.name,
            "source": self.source,
            "title": self.title,
            "content": self.content,
            "char_length": self.char_length,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "provenance": self.provenance,
            "metadata": self.metadata,
        }


@dataclass
class ContextBudget:
    max_total_chars: int = 8000
    reserved_user_prompt_chars: int = 1500
    reserved_persona_chars: int = 1000
    max_history_turns: int = 10


@dataclass
class PersonalizedContextBundle:
    request_id: str
    user_prompt: str
    user_name: str
    interaction_style: str
    assembled_prompt: str
    included_items: list[ContextItem] = field(default_factory=list)
    dropped_items: list[ContextItem] = field(default_factory=list)
    total_characters: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_prompt": self.user_prompt,
            "user_name": self.user_name,
            "interaction_style": self.interaction_style,
            "assembled_prompt": self.assembled_prompt,
            "included_items": [i.to_dict() for i in self.included_items],
            "dropped_items": [i.to_dict() for i in self.dropped_items],
            "total_characters": self.total_characters,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }
