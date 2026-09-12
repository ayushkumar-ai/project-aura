"""M32 — Context & Personalization Engine for Project AURA.

Constructs bounded, prioritized, and personalized context windows combining user preferences,
task state, multi-source RAG results, conversation history, and user requests.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.context_personalization_types import (
    ContextBudget,
    ContextItem,
    ContextPriority,
    PersonalizedContextBundle,
)
from core.personal_state_types import UserPreferences
from core.retrieval_types import RetrievalContextBundle
from core.security_scrubber import scrub_string

logger = logging.getLogger("aura.context_personalization")


class ContextPersonalizationEngine:
    """Unified context intelligence and dynamic personalization engine."""

    def __init__(self, default_budget: ContextBudget | None = None):
        self.default_budget = default_budget or ContextBudget()
        self._lock = threading.RLock()

    def build_context_bundle(
        self,
        user_prompt: str,
        user_preferences: UserPreferences | None = None,
        retrieval_bundle: RetrievalContextBundle | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        task_state: dict[str, Any] | None = None,
        custom_items: list[ContextItem] | None = None,
        budget: ContextBudget | None = None,
        request_id: str | None = None,
    ) -> PersonalizedContextBundle:
        """Construct a bounded, prioritized, and personalized context bundle."""
        with self._lock:
            effective_budget = budget or self.default_budget
            req_id = request_id or f"ctx_{uuid4().hex[:12]}"
            prefs = user_preferences or UserPreferences()

            candidate_items: list[ContextItem] = []

            # 1. Critical Item: User Prompt (Top Priority)
            clean_prompt = scrub_string(user_prompt.strip())
            candidate_items.append(
                ContextItem(
                    item_id=f"item_prompt_{req_id}",
                    priority=ContextPriority.USER_INSTRUCTION,
                    source="user_request",
                    title="User Prompt",
                    content=clean_prompt,
                )
            )

            # 2. Task State (if present)
            if task_state:
                task_lines = [f"- {k}: {v}" for k, v in task_state.items()]
                task_content = "\n".join(task_lines)
                candidate_items.append(
                    ContextItem(
                        item_id=f"item_task_{req_id}",
                        priority=ContextPriority.TASK_STATE,
                        source="task_state",
                        title="Active Task State",
                        content=task_content,
                    )
                )

            # 3. User Preferences & Personalization Directives
            pref_lines = [
                f"User Name: {prefs.preferred_name}",
                f"Style: {prefs.interaction_style} (Verbosity: {prefs.verbosity}/5)",
            ]
            if prefs.custom_rules:
                pref_lines.append("Rules:\n" + "\n".join(f"- {r}" for r in prefs.custom_rules))
            candidate_items.append(
                ContextItem(
                    item_id=f"item_prefs_{req_id}",
                    priority=ContextPriority.USER_PREFERENCES,
                    source="user_preferences",
                    title="Personalization Preferences",
                    content="\n".join(pref_lines),
                )
            )

            # 4. Retrieved Facts & RAG Candidates
            if retrieval_bundle and retrieval_bundle.candidates:
                for idx, cand in enumerate(retrieval_bundle.candidates):
                    priority = (
                        ContextPriority.RETRIEVED_FACTS
                        if cand.source_type.value in ("knowledge_base", "epistemic_graph", "personal_memory")
                        else ContextPriority.EXPERIENCE_HEURISTICS
                    )
                    candidate_items.append(
                        ContextItem(
                            item_id=f"item_rag_{idx}_{cand.candidate_id}",
                            priority=priority,
                            source=cand.source_type.value,
                            title=cand.title,
                            content=cand.text,
                            confidence=cand.confidence,
                            provenance=cand.provenance,
                        )
                    )

            # 5. Conversation History
            if conversation_history:
                recent_turns = conversation_history[-effective_budget.max_history_turns :]
                hist_lines = []
                for turn in recent_turns:
                    role = turn.get("role", "user").capitalize()
                    msg = scrub_string(turn.get("content", ""))
                    hist_lines.append(f"{role}: {msg}")
                if hist_lines:
                    candidate_items.append(
                        ContextItem(
                            item_id=f"item_hist_{req_id}",
                            priority=ContextPriority.CONVERSATION_HISTORY,
                            source="conversation_history",
                            title="Recent Conversation History",
                            content="\n".join(hist_lines),
                        )
                    )

            # 6. Custom Items (if provided)
            if custom_items:
                candidate_items.extend(custom_items)

            # Sort by priority ascending (lower IntEnum value = higher priority), then timestamp
            candidate_items.sort(key=lambda item: (int(item.priority), -item.confidence))

            # Bounded Selection within Character Budget
            included: list[ContextItem] = []
            dropped: list[ContextItem] = []
            used_chars = 0

            for item in candidate_items:
                item_len = len(item.content) + len(item.title) + 50  # buffer for formatting
                if used_chars + item_len <= effective_budget.max_total_chars:
                    included.append(item)
                    used_chars += item_len
                else:
                    # If this is critical/user instruction and we have room, truncate or include
                    if item.priority in (ContextPriority.CRITICAL, ContextPriority.USER_INSTRUCTION):
                        included.append(item)
                        used_chars += item_len
                    else:
                        dropped.append(item)

            # Format the final assembled prompt text
            assembled_prompt = self._render_assembled_prompt(included, prefs, clean_prompt)

            return PersonalizedContextBundle(
                request_id=req_id,
                user_prompt=clean_prompt,
                user_name=prefs.preferred_name,
                interaction_style=prefs.interaction_style,
                assembled_prompt=assembled_prompt,
                included_items=included,
                dropped_items=dropped,
                total_characters=len(assembled_prompt),
                created_at=time.time(),
            )

    def _render_assembled_prompt(
        self,
        items: list[ContextItem],
        prefs: UserPreferences,
        user_prompt: str,
    ) -> str:
        """Render clean, structured prompt with distinct contextual sections."""
        sections: list[str] = []

        # System / Persona Header
        sections.append(
            f"=== AURA CONTEXT INTELLIGENCE ===\n"
            f"User: {prefs.preferred_name} | Target Style: {prefs.interaction_style} | Verbosity: {prefs.verbosity}/5"
        )

        # Non-prompt items grouped by section
        context_items = [i for i in items if i.priority != ContextPriority.USER_INSTRUCTION]
        if context_items:
            sections.append("--- CONTEXT & KNOWLEDGE ---")
            for item in context_items:
                sections.append(f"[{item.title}] ({item.source})\n{item.content}")

        # Final User Request
        sections.append(f"--- USER REQUEST ---\n{user_prompt}")

        return "\n\n".join(sections)
