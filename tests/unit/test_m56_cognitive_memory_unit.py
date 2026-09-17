"""Unit tests for M56 Cognitive Memory Domain Types, Models, and Sanitization.

Validates domain invariants:
- M56-F02: Category Partition Invariance
- M56-F03: Confidence Range Bounding [0.0, 1.0]
- M56-F08: Taint Envelope Propagation
- M56-F17: PII & Sensitive Secret Scrubbing
- M56-F34: Safe Deserialization
"""

import pytest
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    ExperiencePattern,
    FeedbackType,
    LifecycleState,
    MemoryContradiction,
    MemoryFeedbackEvent,
    ProvenanceType,
    ResolutionStrategy,
    scrub_sensitive_content,
)


class TestCognitiveMemoryTypes:
    """Test suite for domain models and data contracts."""

    def test_cognitive_memory_defaults_and_validation(self):
        mem = CognitiveMemory(
            memory_id="mem_001",
            tenant_id="tenant_alpha",
            memory_type=CognitiveMemoryType.SEMANTIC,
            content="User prefers Python over Java.",
            key="pref:language",
        )
        assert mem.memory_id == "mem_001"
        assert mem.tenant_id == "tenant_alpha"
        assert mem.memory_type == CognitiveMemoryType.SEMANTIC
        assert mem.confidence == 1.0
        assert mem.lifecycle_state == LifecycleState.ACTIVE
        assert mem.provenance_type == ProvenanceType.SYSTEM_DERIVED
        assert mem.taint_status is False
        assert mem.version == 1

    def test_confidence_range_bounding(self):
        """Invariant M56-F03: Confidence score must be strictly bounded in [0.0, 1.0]."""
        mem_high = CognitiveMemory(
            memory_id="mem_high",
            tenant_id="tenant_alpha",
            confidence=1.5,
        )
        assert mem_high.confidence == 1.0

        mem_low = CognitiveMemory(
            memory_id="mem_low",
            tenant_id="tenant_alpha",
            confidence=-0.8,
        )
        assert mem_low.confidence == 0.0

    def test_taint_propagation_for_external_source(self):
        """Invariant M56-F08: External imported memories must set taint_status = True."""
        mem = CognitiveMemory(
            memory_id="mem_ext",
            tenant_id="tenant_alpha",
            provenance_type=ProvenanceType.EXTERNAL_IMPORTED,
            source_urls=("https://example.com/api",),
        )
        assert mem.taint_status is True
        assert mem.source_urls == ("https://example.com/api",)

    def test_secret_scrubbing_on_content(self):
        """Invariant M56-F17: Credentials and bearer tokens must be redacted."""
        raw_text = "Connecting with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and api_key='sk_test_1234567890abcdef'"
        scrubbed = scrub_sensitive_content(raw_text)
        assert "[REDACTED_SECRET]" in scrubbed
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in scrubbed
        assert "sk_test_1234567890abcdef" not in scrubbed

        # Verify cognitive memory auto-scrubs on initialization
        mem = CognitiveMemory(
            memory_id="mem_sec",
            tenant_id="tenant_alpha",
            content="password = supersecretpassword123",
        )
        assert "supersecretpassword123" not in mem.content
        assert "[REDACTED_SECRET]" in mem.content

    def test_metadata_permission_sanitization(self):
        """Metadata must strip forbidden elevation keys and callables."""
        unsafe_meta = {
            "is_approved": True,
            "system_override": True,
            "valid_key": "safe_value",
            "callable_key": lambda: "hacked",
        }
        mem = CognitiveMemory(
            memory_id="mem_meta",
            tenant_id="tenant_alpha",
            metadata=unsafe_meta,
        )
        assert "is_approved" not in mem.metadata
        assert "system_override" not in mem.metadata
        assert "callable_key" not in mem.metadata
        assert mem.metadata["valid_key"] == "safe_value"

    def test_serialization_and_deserialization_roundtrip(self):
        """Invariant M56-F34: Safe serialization and deserialization."""
        original = CognitiveMemory(
            memory_id="mem_rt",
            tenant_id="tenant_beta",
            memory_type=CognitiveMemoryType.PREFERENCE,
            category="ui",
            key="theme",
            content="dark",
            confidence=0.95,
            provenance_type=ProvenanceType.USER_EXPLICIT,
            lifecycle_state=LifecycleState.ACTIVE,
            tags=("ui", "dark_mode"),
            source_urls=("https://settings.local",),
            metadata={"source": "user_settings"},
        )
        data = original.to_dict()
        restored = CognitiveMemory.from_dict(data)

        assert restored.memory_id == original.memory_id
        assert restored.tenant_id == original.tenant_id
        assert restored.memory_type == original.memory_type
        assert restored.category == original.category
        assert restored.key == original.key
        assert restored.content == original.content
        assert restored.confidence == original.confidence
        assert restored.provenance_type == original.provenance_type
        assert restored.lifecycle_state == original.lifecycle_state
        assert restored.tags == original.tags
        assert restored.source_urls == original.source_urls

    def test_experience_pattern_metrics(self):
        """Invariant M56-F30: Non-negative metrics and success rate calculation."""
        pat = ExperiencePattern(
            pattern_id="pat_01",
            tenant_id="tenant_alpha",
            context_key="deploy_task",
            success_count=8,
            failure_count=2,
            average_latency_ms=125.5,
            optimal_tools=["docker_tool", "k8s_tool"],
        )
        assert pat.total_attempts == 10
        assert pat.success_rate == 0.8
        assert pat.optimal_tools == ["docker_tool", "k8s_tool"]

    def test_memory_feedback_event_model(self):
        ev = MemoryFeedbackEvent(
            event_id="fb_001",
            tenant_id="tenant_alpha",
            target_memory_id="mem_001",
            feedback_type=FeedbackType.CORRECTION,
            correction_content="Updated language is Rust.",
        )
        assert ev.applied is False
        assert ev.feedback_type == FeedbackType.CORRECTION
        assert ev.correction_content == "Updated language is Rust."

        d = ev.to_dict()
        restored = MemoryFeedbackEvent.from_dict(d)
        assert restored.event_id == ev.event_id
        assert restored.feedback_type == FeedbackType.CORRECTION
