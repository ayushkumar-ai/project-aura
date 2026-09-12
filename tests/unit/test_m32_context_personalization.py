"""Unit tests for M32 Context & Personalization Engine Subsystem."""

from core.context_personalization_engine import ContextPersonalizationEngine
from core.context_personalization_types import ContextBudget, ContextItem, ContextPriority
from core.personal_state_types import UserPreferences
from core.retrieval_types import (
    AuthorityTier,
    RetrievalCandidate,
    RetrievalContextBundle,
    RetrievalSourceType,
)


def test_build_context_bundle_basic():
    engine = ContextPersonalizationEngine()
    prefs = UserPreferences(preferred_name="Charlie", interaction_style="concise", verbosity=2)

    bundle = engine.build_context_bundle(
        user_prompt="Help me configure Docker compose.",
        user_preferences=prefs,
    )

    assert bundle.user_name == "Charlie"
    assert bundle.interaction_style == "concise"
    assert "User: Charlie" in bundle.assembled_prompt
    assert "Help me configure Docker compose." in bundle.assembled_prompt
    assert len(bundle.included_items) >= 2


def test_context_budget_and_prioritization():
    # Set a tiny budget to verify that low-priority items are dropped while high-priority are kept
    budget = ContextBudget(max_total_chars=600)
    engine = ContextPersonalizationEngine(default_budget=budget)

    prefs = UserPreferences(preferred_name="David")
    rag_candidate = RetrievalCandidate(
        candidate_id="c1",
        source_type=RetrievalSourceType.KNOWLEDGE_BASE,
        title="Very Long Knowledge Document",
        text="A" * 1000,  # 1000 characters
        score=0.9,
    )
    rag_bundle = RetrievalContextBundle(
        query="Test query",
        candidates=[rag_candidate],
    )

    bundle = engine.build_context_bundle(
        user_prompt="Short prompt",
        user_preferences=prefs,
        retrieval_bundle=rag_bundle,
        budget=budget,
    )

    # Prompt and prefs should be included; the 1000-char RAG document should be dropped due to budget
    included_sources = {i.source for i in bundle.included_items}
    assert "user_request" in included_sources
    assert len(bundle.dropped_items) >= 1
    assert bundle.dropped_items[0].source == "knowledge_base"


def test_context_secret_scrubbing():
    engine = ContextPersonalizationEngine()
    prompt_with_secret = "Here is my key: sk-TestKey123456789012345678 please analyze it."

    bundle = engine.build_context_bundle(user_prompt=prompt_with_secret)
    assert "sk-TestKey123456789012345678" not in bundle.assembled_prompt
    assert "[REDACTED]" in bundle.assembled_prompt
