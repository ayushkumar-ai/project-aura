"""Unit tests for Milestone 28 Epistemic Knowledge Graph Store."""

import pytest
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeGraphQuery,
    KnowledgeRelation,
    RelationType,
)


def _make_entity(eid: str, etype: EntityType, name: str = "", caps: list[str] | None = None) -> KnowledgeEntity:
    props = {}
    if caps:
        props["capabilities"] = caps
    return KnowledgeEntity(
        entity_id=eid,
        entity_type=etype,
        name=name or eid,
        properties=props,
        confidence=1.0,
    )


def test_graph_add_get_remove_entities():
    graph = EpistemicKnowledgeGraph()
    e1 = _make_entity("goal_1", EntityType.GOAL, "First Goal")
    e2 = _make_entity("skill_1", EntityType.SKILL, "Calculator", caps=["math", "calc"])

    graph.add_entity(e1)
    graph.add_entity(e2)

    assert graph.has_entity("goal_1")
    assert graph.has_entity("skill_1")
    assert not graph.has_entity("nonexistent")

    retrieved = graph.get_entity("goal_1")
    assert retrieved is not None
    assert retrieved.name == "First Goal"
    assert retrieved.access_count == 1  # Updated on access

    # Remove entity
    assert graph.remove_entity("goal_1") is True
    assert not graph.has_entity("goal_1")
    assert graph.remove_entity("goal_1") is False


def test_graph_add_get_remove_relations():
    graph = EpistemicKnowledgeGraph()
    e1 = _make_entity("goal_1", EntityType.GOAL)
    e2 = _make_entity("role_1", EntityType.ROLE)
    graph.add_entity(e1)
    graph.add_entity(e2)

    rel = KnowledgeRelation(
        relation_id="rel_1",
        source_id="goal_1",
        target_id="role_1",
        relation_type=RelationType.DELEGATED_TO,
        weight=1.0,
    )
    graph.add_relation(rel)

    assert graph.get_relation("rel_1") is not None
    assert len(graph.get_outgoing_relations("goal_1")) == 1
    assert len(graph.get_incoming_relations("role_1")) == 1

    # Reinforce on duplicate add
    rel_dup = KnowledgeRelation(
        relation_id="rel_dup",
        source_id="goal_1",
        target_id="role_1",
        relation_type=RelationType.DELEGATED_TO,
    )
    reinforced = graph.add_relation(rel_dup, reinforce_if_exists=True)
    assert reinforced.relation_id == "rel_1"
    assert reinforced.evidence_count == 2
    assert reinforced.weight > 1.0

    # Remove relation
    assert graph.remove_relation("rel_1") is True
    assert len(graph.get_outgoing_relations("goal_1")) == 0


def test_graph_multi_hop_subgraph_traversal():
    graph = EpistemicKnowledgeGraph()
    # Chain: A -> B -> C -> D
    a = _make_entity("a", EntityType.GOAL)
    b = _make_entity("b", EntityType.GOAL)
    c = _make_entity("c", EntityType.SKILL)
    d = _make_entity("d", EntityType.ARTIFACT)

    for node in (a, b, c, d):
        graph.add_entity(node)

    graph.add_relation(KnowledgeRelation("r_ab", "a", "b", RelationType.DEPENDS_ON))
    graph.add_relation(KnowledgeRelation("r_bc", "b", "c", RelationType.PRODUCED_BY))
    graph.add_relation(KnowledgeRelation("r_cd", "c", "d", RelationType.CONSUMED_BY))

    # Depth 1 from A
    sub1 = graph.traverse_subgraph("a", max_depth=1)
    e_ids1 = {e.entity_id for e in sub1.entities}
    assert e_ids1 == {"a", "b"}

    # Depth 2 from A
    sub2 = graph.traverse_subgraph("a", max_depth=2)
    e_ids2 = {e.entity_id for e in sub2.entities}
    assert e_ids2 == {"a", "b", "c"}

    # Depth 3 from A
    sub3 = graph.traverse_subgraph("a", max_depth=3)
    e_ids3 = {e.entity_id for e in sub3.entities}
    assert e_ids3 == {"a", "b", "c", "d"}


def test_graph_shortest_path_finding():
    graph = EpistemicKnowledgeGraph()
    for name in ["start", "mid1", "mid2", "target"]:
        graph.add_entity(_make_entity(name, EntityType.GOAL))

    graph.add_relation(KnowledgeRelation("r1", "start", "mid1", RelationType.DEPENDS_ON))
    graph.add_relation(KnowledgeRelation("r2", "mid1", "target", RelationType.DEPENDS_ON))
    graph.add_relation(KnowledgeRelation("r3", "start", "mid2", RelationType.DEPENDS_ON))

    path = graph.find_path("start", "target")
    assert len(path) == 3
    assert [e.entity_id for e in path] == ["start", "mid1", "target"]

    # Unreachable
    assert graph.find_path("mid2", "target") == []


def test_graph_capacity_bounds_and_pruning():
    # Small capacity graph
    graph = EpistemicKnowledgeGraph(max_entities=5, max_relations=5)

    for i in range(10):
        ent = KnowledgeEntity(
            entity_id=f"e_{i}",
            entity_type=EntityType.GOAL,
            name=f"Entity {i}",
            confidence=float(i) / 10.0,
        )
        graph.add_entity(ent)

    # Must not exceed max_entities
    assert len(graph._entities) <= 5
    # High confidence entities should be retained
    assert graph.has_entity("e_9")
    assert graph.has_entity("e_8")


def test_graph_serialization_roundtrip():
    graph1 = EpistemicKnowledgeGraph()
    e1 = _make_entity("e1", EntityType.GOAL, "Goal A")
    e2 = _make_entity("e2", EntityType.SKILL, "Skill B")
    graph1.add_entity(e1)
    graph1.add_entity(e2)
    graph1.add_relation(KnowledgeRelation("r1", "e1", "e2", RelationType.PRODUCED_BY))

    data = graph1.to_dict()
    assert "entities" in data
    assert "relations" in data

    graph2 = EpistemicKnowledgeGraph.from_dict(data)
    assert graph2.has_entity("e1")
    assert graph2.has_entity("e2")
    assert graph2.get_relation("r1") is not None
    assert len(graph2.get_outgoing_relations("e1")) == 1
