"""Unit tests for Milestone 28 Epistemic Knowledge Graph Type Contracts."""

import pytest
import time
from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeGraphQuery,
    KnowledgeGraphSubgraph,
    KnowledgeRelation,
    RelationType,
    _sanitize_graph_metadata,
)
from core.provenance import TaintedValue, wrap_tainted


def test_entity_and_relation_enums():
    assert EntityType.GOAL == "goal"
    assert EntityType.ROLE == "role"
    assert EntityType.SKILL == "skill"
    assert EntityType.ARTIFACT == "artifact"
    assert EntityType.FAULT_PATTERN == "fault_pattern"
    assert EntityType.REMEDIATION_RECIPE == "remediation_recipe"
    assert EntityType.EXECUTION_PATTERN == "execution_pattern"

    assert RelationType.PRODUCED_BY == "produced_by"
    assert RelationType.CONSUMED_BY == "consumed_by"
    assert RelationType.DELEGATED_TO == "delegated_to"
    assert RelationType.RESOLVED_BY == "resolved_by"
    assert RelationType.CAUSED_BY == "caused_by"
    assert RelationType.SPECIALIZES == "specializes"
    assert RelationType.DEPENDS_ON == "depends_on"
    assert RelationType.COLLABORATED_WITH == "collaborated_with"
    assert RelationType.SIMILAR_TO == "similar_to"


def test_sanitize_graph_metadata_strips_forbidden_keys():
    raw = {
        "valid_key": "safe",
        "is_admin": True,
        "sudo": "root",
        "bypass_policy": True,
        "approved": True,
        "is_authorized": True,
        "count": 10,
    }
    cleaned = _sanitize_graph_metadata(raw)
    assert cleaned["valid_key"] == "safe"
    assert cleaned["count"] == 10
    assert "is_admin" not in cleaned
    assert "sudo" not in cleaned
    assert "bypass_policy" not in cleaned
    assert "approved" not in cleaned
    assert "is_authorized" not in cleaned


def test_knowledge_entity_immutability_and_serialization():
    t0 = time.time()
    ent = KnowledgeEntity(
        entity_id="goal_1",
        entity_type=EntityType.GOAL,
        name="Extract Dataset",
        properties={"domain": "data_engineering", "priority": "high"},
        confidence=0.95,
        created_at=t0,
        access_count=2,
    )

    assert ent.entity_id == "goal_1"
    assert ent.entity_type == EntityType.GOAL
    assert ent.confidence == 0.95
    assert ent.access_count == 2
    assert ent.is_untrusted is False

    # Immutability check
    with pytest.raises(Exception):
        ent.name = "New Name"  # type: ignore

    # Access update
    accessed = ent.with_access()
    assert accessed.access_count == 3
    assert accessed.entity_id == ent.entity_id

    # Serialization roundtrip
    d = ent.to_dict()
    restored = KnowledgeEntity.from_dict(d)
    assert restored.entity_id == ent.entity_id
    assert restored.entity_type == EntityType.GOAL
    assert restored.name == "Extract Dataset"
    assert restored.confidence == 0.95
    assert restored.properties == ent.properties


def test_knowledge_entity_taint_propagation():
    tainted = wrap_tainted("untrusted_source_code", is_untrusted=True, source_type="user_input")
    ent = KnowledgeEntity(
        entity_id="skill_untrusted",
        entity_type=EntityType.SKILL,
        name="Untrusted Tool",
        properties={"source": tainted},
    )
    assert ent.is_untrusted is True


def test_knowledge_relation_immutability_and_serialization():
    rel = KnowledgeRelation(
        relation_id="rel_1",
        source_id="goal_1",
        target_id="role_engineer",
        relation_type=RelationType.DELEGATED_TO,
        weight=1.5,
        confidence=0.85,
        evidence_count=2,
    )

    assert rel.relation_id == "rel_1"
    assert rel.source_id == "goal_1"
    assert rel.target_id == "role_engineer"
    assert rel.relation_type == RelationType.DELEGATED_TO
    assert rel.weight == 1.5
    assert rel.confidence == 0.85
    assert rel.evidence_count == 2

    # Reinforce
    reinforced = rel.with_reinforcement(weight_delta=0.2, confidence_boost=0.05)
    assert reinforced.weight == 1.7
    assert reinforced.confidence == 0.90
    assert reinforced.evidence_count == 3

    # Serialization
    d = rel.to_dict()
    restored = KnowledgeRelation.from_dict(d)
    assert restored.relation_id == rel.relation_id
    assert restored.relation_type == RelationType.DELEGATED_TO
    assert restored.weight == 1.5


def test_knowledge_graph_query_and_subgraph():
    q = KnowledgeGraphQuery(
        entity_types=(EntityType.SKILL,),
        keyword="calc",
        max_depth=3,
        min_confidence=0.5,
        limit=10,
    )
    assert q.entity_types == (EntityType.SKILL,)
    assert q.keyword == "calc"
    assert q.max_depth == 3
    assert q.min_confidence == 0.5
    assert q.limit == 10

    e1 = KnowledgeEntity(entity_id="e1", entity_type=EntityType.GOAL, name="Goal 1")
    r1 = KnowledgeRelation(
        relation_id="r1", source_id="e1", target_id="e1", relation_type=RelationType.DEPENDS_ON
    )
    sub = KnowledgeGraphSubgraph(entities=(e1,), relations=(r1,), root_entity_id="e1")
    assert len(sub.entities) == 1
    assert len(sub.relations) == 1

    d = sub.to_dict()
    restored = KnowledgeGraphSubgraph.from_dict(d)
    assert len(restored.entities) == 1
    assert restored.root_entity_id == "e1"
