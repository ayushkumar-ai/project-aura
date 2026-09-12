"""Unit tests for Milestone 28 Epistemic Query & Semantic Recommendation Engine."""

import pytest
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_query_engine import EpistemicQueryEngine
from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeRelation,
    RelationType,
)
from core.fault_types import FaultCategory


def test_recommend_remediation_ranking():
    graph = EpistemicKnowledgeGraph()
    query_engine = EpistemicQueryEngine(knowledge_graph=graph)

    # Add fault entity
    fault = KnowledgeEntity(
        entity_id="fault_transient_infrastructure_p1",
        entity_type=EntityType.FAULT_PATTERN,
        name="Fault: transient_infrastructure",
        properties={"category": "transient_infrastructure"},
        confidence=0.9,
    )
    graph.add_entity(fault)

    # Recipe 1: Successful retry (high score)
    r1 = KnowledgeEntity(
        entity_id="recipe_retry_1",
        entity_type=EntityType.REMEDIATION_RECIPE,
        name="Retry Recipe",
        properties={"action_types": ["retry_phase"], "is_recovered": True},
        confidence=0.95,
    )
    graph.add_entity(r1)
    graph.add_relation(
        KnowledgeRelation("rel_f_r1", fault.entity_id, r1.entity_id, RelationType.RESOLVED_BY, weight=1.5)
    )

    # Recipe 2: Failed retry (low score)
    r2 = KnowledgeEntity(
        entity_id="recipe_replan_2",
        entity_type=EntityType.REMEDIATION_RECIPE,
        name="Replan Recipe",
        properties={"action_types": ["replan_goal"], "is_recovered": False},
        confidence=0.4,
    )
    graph.add_entity(r2)
    graph.add_relation(
        KnowledgeRelation("rel_f_r2", fault.entity_id, r2.entity_id, RelationType.RESOLVED_BY, weight=0.5)
    )

    recs = query_engine.recommend_remediation(FaultCategory.TRANSIENT_INFRASTRUCTURE)
    assert len(recs) == 2
    # First recommendation should be r1 with higher composite score
    assert recs[0]["recipe_id"] == "recipe_retry_1"
    assert recs[0]["score"] > recs[1]["score"]
    assert recs[0]["is_recovered"] is True


def test_recommend_skills_by_capability_and_verification():
    graph = EpistemicKnowledgeGraph()
    query_engine = EpistemicQueryEngine(knowledge_graph=graph)

    s1 = KnowledgeEntity(
        entity_id="skill_calc_verified",
        entity_type=EntityType.SKILL,
        name="math_calculator",
        properties={"capabilities": ["math", "calculation"], "is_verified": True},
        confidence=0.95,
    )
    s2 = KnowledgeEntity(
        entity_id="skill_calc_unverified",
        entity_type=EntityType.SKILL,
        name="rough_calculator",
        properties={"capabilities": ["math"], "is_verified": False},
        confidence=0.6,
    )
    graph.add_entity(s1)
    graph.add_entity(s2)

    recs = query_engine.recommend_skills(required_capabilities=("math", "calculation"))
    assert len(recs) >= 1
    assert recs[0]["skill_id"] == "skill_calc_verified"
    assert recs[0]["is_verified"] is True


def test_recommend_role_allocation():
    graph = EpistemicKnowledgeGraph()
    query_engine = EpistemicQueryEngine(knowledge_graph=graph)

    r1 = KnowledgeEntity(
        entity_id="role_architect",
        entity_type=EntityType.ROLE,
        name="System Architect",
        properties={"capabilities": ["system_design", "pipeline_planning"]},
        confidence=0.9,
    )
    r2 = KnowledgeEntity(
        entity_id="role_coder",
        entity_type=EntityType.ROLE,
        name="Python Developer",
        properties={"capabilities": ["coding", "debugging"]},
        confidence=0.85,
    )
    graph.add_entity(r1)
    graph.add_entity(r2)

    recs = query_engine.recommend_role_allocation(
        goal_title="Design multi-agent mission pipeline",
        required_capabilities=("system_design",),
    )
    assert len(recs) >= 1
    assert recs[0]["role_id"] == "role_architect"


def test_find_proven_goal_patterns():
    graph = EpistemicKnowledgeGraph()
    query_engine = EpistemicQueryEngine(knowledge_graph=graph)

    ep = KnowledgeEntity(
        entity_id="ep_data_pipeline",
        entity_type=EntityType.EXECUTION_PATTERN,
        name="ETL Pipeline Pattern",
        properties={
            "status": "completed",
            "completed_phases": ["extract", "transform", "load"],
            "duration_seconds": 15.0,
        },
        confidence=0.95,
    )
    graph.add_entity(ep)

    patterns = query_engine.find_proven_goal_patterns(goal_domain="pipeline")
    assert len(patterns) == 1
    assert patterns[0]["pattern_id"] == "ep_data_pipeline"
    assert patterns[0]["completed_phases"] == ["extract", "transform", "load"]
