"""Unit tests for M56 Cognitive Personalization Engine and Context Injector.

Validates invariants:
- M56-F18: Personalization Context Bounds & Truncation
- M56-F22: Deterministic Composite Relevance Ranking
"""

import time
import pytest
from core.cognitive_memory.personalization import PersonalizationEngine
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ExperiencePattern,
    LifecycleState,
    ProvenanceType,
    UserCognitiveProfile,
)


class TestPersonalizationEngine:
    """Test suite for memory ranking and prompt context generation."""

    def test_composite_ranking_determinism(self):
        """Invariant M56-F22: Deterministic scoring and ranking."""
        engine = PersonalizationEngine(weight_relevance=0.5, weight_confidence=0.3, weight_recency=0.2)
        now = time.time()

        mem_exact = CognitiveMemory(
            memory_id="m_exact",
            tenant_id="tenant_alpha",
            key="python_style",
            content="Prefer Python with type annotations and black formatting",
            tags=("python", "style"),
            confidence=1.0,
            last_accessed_at=now,
        )

        mem_unrelated = CognitiveMemory(
            memory_id="m_unrel",
            tenant_id="tenant_alpha",
            key="food_pref",
            content="User likes pizza",
            confidence=0.5,
            last_accessed_at=now - 86400,
        )

        res_exact = engine.score_memory(mem_exact, query="python type annotations", current_time=now)
        res_unrel = engine.score_memory(mem_unrelated, query="python type annotations", current_time=now)

        assert res_exact.score > res_unrel.score
        assert res_exact.relevance_score > res_unrel.relevance_score

    def test_context_injector_prompt_assembly(self):
        """Builds structured prompt block from profile, memories, and experience patterns."""
        engine = PersonalizationEngine()
        profile = UserCognitiveProfile(
            profile_id="p1",
            tenant_id="tenant_alpha",
            preferences={"interaction_style": "concise", "editor": "neovim"},
            inferred_traits={"expertise": "senior_systems_engineer"},
        )

        memories = [
            CognitiveMemory(
                memory_id="m1",
                tenant_id="tenant_alpha",
                memory_type=CognitiveMemoryType.PREFERENCE,
                key="code_formatting",
                content="Use ruff and black",
                confidence=1.0,
            ),
            CognitiveMemory(
                memory_id="m2",
                tenant_id="tenant_alpha",
                memory_type=CognitiveMemoryType.SEMANTIC,
                category="infrastructure",
                content="Production database is PostgreSQL 16 on port 5432",
                confidence=0.9,
            ),
        ]

        patterns = [
            ExperiencePattern(
                pattern_id="exp1",
                tenant_id="tenant_alpha",
                context_key="backend_development",
                optimal_tools=["pytest_tool", "alembic_tool"],
                success_count=10,
            )
        ]

        ctx = engine.build_personalization_context(
            tenant_id="tenant_alpha",
            profile=profile,
            memories=memories,
            experience_patterns=patterns,
            query="backend database",
        )

        assert "[USER PERSONALIZATION & COGNITIVE CONTEXT]" in ctx.formatted_prompt_block
        assert "interaction_style: concise" in ctx.formatted_prompt_block
        assert "expertise: senior_systems_engineer" in ctx.formatted_prompt_block
        assert "PostgreSQL 16" in ctx.formatted_prompt_block
        assert "pytest_tool" in ctx.formatted_prompt_block

    def test_context_bounds_and_truncation(self):
        """Invariant M56-F18: Maximum character limit bounds must be strictly enforced."""
        engine = PersonalizationEngine()
        profile = UserCognitiveProfile(
            profile_id="p2",
            tenant_id="tenant_alpha",
            preferences={f"key_{i}": "very_long_preference_value_" * 10 for i in range(20)},
        )

        ctx = engine.build_personalization_context(
            tenant_id="tenant_alpha",
            profile=profile,
            max_context_chars=500,
        )

        assert len(ctx.formatted_prompt_block) <= 520
        assert "[TRUNCATED]" in ctx.formatted_prompt_block
