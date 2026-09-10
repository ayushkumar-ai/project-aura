"""
Unit tests for Milestone 15 core/lifecycle_types.py.
Tests memory lifecycle contracts, enums, dataclasses, serialization,
efficacy scoring, and security sanitization.
"""

import pytest
import time
from core.lifecycle_types import (
    DecayModel,
    LifecycleAction,
    RuleStatus,
    DecayConfig,
    MemoryUtilityScore,
    RuleEfficacyRecord,
    CompactionRecord,
    strip_forbidden_metadata_keys,
    _canonical_value,
    _restore_value,
)
from core.provenance import TaintedValue


class TestLifecycleEnums:
    def test_decay_model_values(self):
        assert DecayModel.EXPONENTIAL.value == "exponential"
        assert DecayModel.LINEAR.value == "linear"
        assert DecayModel.STEP.value == "step"
        assert DecayModel.NONE.value == "none"

    def test_lifecycle_action_values(self):
        assert LifecycleAction.RETAIN.value == "retain"
        assert LifecycleAction.PROMOTE.value == "promote"
        assert LifecycleAction.COMPACT.value == "compact"
        assert LifecycleAction.EVICT.value == "evict"
        assert LifecycleAction.ARCHIVE.value == "archive"

    def test_rule_status_values(self):
        assert RuleStatus.CANDIDATE.value == "candidate"
        assert RuleStatus.ACTIVE.value == "active"
        assert RuleStatus.PROMOTED.value == "promoted"
        assert RuleStatus.DEPRECATED.value == "deprecated"


class TestDecayConfig:
    def test_decay_config_defaults(self):
        config = DecayConfig()
        assert config.decay_model == DecayModel.EXPONENTIAL
        assert config.default_half_life_days == 30.0
        assert config.min_confidence_floor == 0.05
        assert "user_profile" in config.namespace_half_lives
        assert config.namespace_half_lives["user_profile"] == 365.0
        assert config.namespace_half_lives["task_scratchpad"] == 1.0

    def test_get_half_life_days(self):
        config = DecayConfig()
        assert config.get_half_life_days("user_profile") == 365.0
        assert config.get_half_life_days("task:subtask_1") == 1.0
        assert config.get_half_life_days("unknown_namespace") == 30.0


class TestMemoryUtilityScore:
    def test_utility_score_creation_and_dict(self):
        score = MemoryUtilityScore(
            entry_id="fact_001",
            key="user_city",
            namespace="user_profile",
            tier="semantic",
            base_confidence=0.95,
            decayed_confidence=0.85,
            access_count=7,
            recency_score=0.90,
            utility_score=0.82,
            recommended_action=LifecycleAction.RETAIN,
            metadata={"source": "user_input"},
        )
        assert score.entry_id == "fact_001"
        assert score.key == "user_city"
        assert score.effective_confidence if hasattr(score, "effective_confidence") else score.decayed_confidence == 0.85
        assert score.recommended_action == LifecycleAction.RETAIN

        d = score.to_dict()
        assert d["entry_id"] == "fact_001"
        assert d["key"] == "user_city"
        assert d["recommended_action"] == "retain"
        assert d["utility_score"] == 0.82

        restored = MemoryUtilityScore.from_dict(d)
        assert restored.entry_id == "fact_001"
        assert restored.key == "user_city"
        assert restored.recommended_action == LifecycleAction.RETAIN
        assert restored.base_confidence == 0.95


class TestRuleEfficacyRecord:
    def test_rule_efficacy_record_initial(self):
        record = RuleEfficacyRecord(
            rule_id="rule_123",
            trigger_condition="subproc_preferred",
            status=RuleStatus.CANDIDATE,
            base_confidence=0.85,
            calibrated_confidence=0.85,
        )
        assert record.trigger_count == 0
        assert record.success_count == 0
        assert record.failure_count == 0
        assert record.efficacy_score == 1.0
        assert record.status == RuleStatus.CANDIDATE

    def test_rule_efficacy_record_serialization_roundtrip(self):
        record = RuleEfficacyRecord(
            rule_id="rule_456",
            trigger_condition="regex_cleaner",
            status=RuleStatus.PROMOTED,
            trigger_count=5,
            success_count=4,
            failure_count=1,
            replan_count=1,
            base_confidence=0.85,
            calibrated_confidence=0.80,
            efficacy_score=0.80,
            metadata={"domain": "text_processing"},
        )
        d = record.to_dict()
        assert d["rule_id"] == "rule_456"
        assert d["status"] == "promoted"
        assert d["metadata"]["domain"] == "text_processing"

        restored = RuleEfficacyRecord.from_dict(d)
        assert restored.rule_id == "rule_456"
        assert restored.trigger_condition == "regex_cleaner"
        assert restored.status == RuleStatus.PROMOTED
        assert restored.calibrated_confidence == 0.80


class TestCompactionRecord:
    def test_compaction_record_serialization_roundtrip(self):
        rec = CompactionRecord(
            compaction_id="compact_123",
            namespace="user_profile",
            facts_analyzed=10,
            facts_merged=3,
            facts_evicted=2,
            memory_reclaimed_entries=5,
            metadata={"trigger": "quota_exceeded"},
        )
        d = rec.to_dict()
        assert d["compaction_id"] == "compact_123"
        assert d["namespace"] == "user_profile"
        assert d["facts_analyzed"] == 10
        assert d["facts_merged"] == 3
        assert d["facts_evicted"] == 2
        assert d["memory_reclaimed_entries"] == 5

        restored = CompactionRecord.from_dict(d)
        assert restored.compaction_id == "compact_123"
        assert restored.namespace == "user_profile"
        assert restored.facts_merged == 3
        assert restored.memory_reclaimed_entries == 5


class TestSecurityAndTaintHelpers:
    def test_strip_forbidden_metadata_keys(self):
        meta = {
            "author": "Alice",
            "role": "admin",
            "permission": "root",
            "approved": True,
            "approval_status": "authorized",
            "auto_approve": True,
            "authorized": True,
            "bypass_policy": True,
            "role_override": "superadmin",
            "valid_description": "User notes",
            "trust_score": 0.95,
        }
        sanitized = strip_forbidden_metadata_keys(meta)
        assert "approved" not in sanitized
        assert "approval_status" not in sanitized
        assert "auto_approve" not in sanitized
        assert "permission" not in sanitized
        assert "authorized" not in sanitized
        assert "bypass_policy" not in sanitized
        assert "role_override" not in sanitized
        assert sanitized["author"] == "Alice"
        assert sanitized["valid_description"] == "User notes"
        assert sanitized["trust_score"] == 0.95

    def test_canonical_value_and_restore_with_taint(self):
        tainted = TaintedValue(
            raw_value="sensitive data",
            is_untrusted=True,
            source_urls=("https://untrusted.com",),
        )
        canonical = _canonical_value(tainted)
        assert isinstance(canonical, dict)
        assert canonical["__tainted__"] is True
        assert canonical["raw_value"] == "sensitive data"
        assert canonical["is_untrusted"] is True
        assert "https://untrusted.com" in canonical["source_urls"]

        restored = _restore_value(canonical)
        assert isinstance(restored, TaintedValue)
        assert restored.raw_value == "sensitive data"
        assert restored.is_untrusted is True
        assert "https://untrusted.com" in restored.source_urls
