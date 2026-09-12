"""Milestone 28: Epistemic Query & Semantic Recommendation Engine.

Provides domain-specific multi-hop graph retrieval and ranking algorithms:
- Remediation recommendations for diagnosed faults
- Skill recommendations based on required capabilities
- Role allocation recommendations for goal execution
- Proven execution pattern queries for mission planning

Advisory only: never executes actions or bypasses Policy/Approval authorities.
"""

from __future__ import annotations

import logging
from typing import Any

from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeGraphQuery,
    KnowledgeGraphSubgraph,
    RelationType,
)
from core.fault_types import FaultCategory

logger = logging.getLogger("aura.epistemic_query_engine")


class EpistemicQueryEngine:
    """Semantic graph retrieval and recommendation engine for Project AURA."""

    def __init__(
        self,
        knowledge_graph: EpistemicKnowledgeGraph | None = None,
    ) -> None:
        self.knowledge_graph = knowledge_graph if knowledge_graph is not None else EpistemicKnowledgeGraph()

    # ------------------------------------------------------------------
    # Remediation Recommendations (M27 Integration)
    # ------------------------------------------------------------------

    def recommend_remediation(
        self,
        fault_category: str | FaultCategory,
        error_message: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve and rank proven remediation recipes for a given fault category."""
        cat_str = fault_category.value if isinstance(fault_category, FaultCategory) else str(fault_category).strip().lower()
        recommendations: list[dict[str, Any]] = []

        # 1. Query for matching FaultPattern entities
        query = KnowledgeGraphQuery(
            entity_types=(EntityType.FAULT_PATTERN,),
            keyword=cat_str,
            min_confidence=0.0,
            limit=20,
        )
        fault_entities = self.knowledge_graph.query(query)

        # 2. Traverse outgoing RESOLVED_BY relations to find RemediationRecipe entities
        seen_recipe_ids: set[str] = set()
        for fault in fault_entities:
            rels = self.knowledge_graph.get_outgoing_relations(
                source_id=fault.entity_id,
                relation_type=RelationType.RESOLVED_BY,
            )
            for rel in rels:
                recipe = self.knowledge_graph.get_entity(rel.target_id)
                if recipe and recipe.entity_id not in seen_recipe_ids:
                    seen_recipe_ids.add(recipe.entity_id)
                    score = round(recipe.confidence * rel.weight * rel.confidence, 4)
                    recommendations.append({
                        "recipe_id": recipe.entity_id,
                        "action_types": recipe.properties.get("action_types", []),
                        "rationale": recipe.properties.get("rationale", ""),
                        "is_recovered": recipe.properties.get("is_recovered", False),
                        "score": score,
                        "confidence": recipe.confidence,
                        "relation_weight": rel.weight,
                        "evidence_count": rel.evidence_count,
                    })

        # Sort by composite score descending
        recommendations.sort(key=lambda r: r["score"], reverse=True)
        return recommendations[:limit]

    # ------------------------------------------------------------------
    # Skill Recommendations (M26 Integration)
    # ------------------------------------------------------------------

    def recommend_skills(
        self,
        task_description: str = "",
        required_capabilities: tuple[str, ...] = (),
        min_confidence: float = 0.5,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve and rank dynamic skills matching requested capabilities or task context."""
        recommendations: list[dict[str, Any]] = []
        candidate_skills: dict[str, KnowledgeEntity] = {}

        # 1. Look up by explicit capabilities
        for cap in required_capabilities:
            query = KnowledgeGraphQuery(
                entity_types=(EntityType.SKILL,),
                keyword=cap,
                min_confidence=min_confidence,
                limit=limit * 2,
            )
            for ent in self.knowledge_graph.query(query):
                candidate_skills[ent.entity_id] = ent

        # 2. Look up by task description keyword if few matches found
        if task_description and len(candidate_skills) < limit:
            for word in task_description.lower().split()[:5]:
                if len(word) > 3:
                    q = KnowledgeGraphQuery(
                        entity_types=(EntityType.SKILL,),
                        keyword=word,
                        min_confidence=min_confidence,
                        limit=limit,
                    )
                    for ent in self.knowledge_graph.query(q):
                        candidate_skills[ent.entity_id] = ent

        # 3. Score and format candidates
        for sid, skill in candidate_skills.items():
            caps = set(skill.properties.get("capabilities", []))
            matched_caps = caps.intersection({c.lower() for c in required_capabilities})
            match_score = (len(matched_caps) / max(1, len(required_capabilities))) if required_capabilities else 1.0

            is_verified = bool(skill.properties.get("is_verified", False))
            composite_score = round(skill.confidence * (1.2 if is_verified else 0.8) * (0.5 + 0.5 * match_score), 4)

            recommendations.append({
                "skill_id": skill.entity_id,
                "name": skill.name,
                "description": skill.properties.get("description", ""),
                "capabilities": list(caps),
                "is_verified": is_verified,
                "confidence": skill.confidence,
                "score": composite_score,
                "access_count": skill.access_count,
            })

        recommendations.sort(key=lambda s: s["score"], reverse=True)
        return recommendations[:limit]

    # ------------------------------------------------------------------
    # Role Allocation Recommendations (M21 / M22 Integration)
    # ------------------------------------------------------------------

    def recommend_role_allocation(
        self,
        goal_title: str,
        required_capabilities: tuple[str, ...] = (),
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Recommend multi-agent roles with proven capability alignment."""
        recommendations: list[dict[str, Any]] = []

        query = KnowledgeGraphQuery(
            entity_types=(EntityType.ROLE,),
            keyword="",
            min_confidence=0.0,
            limit=20,
        )
        roles = self.knowledge_graph.query(query)

        for role in roles:
            caps = set(role.properties.get("capabilities", []))
            req_set = {c.lower() for c in required_capabilities}
            matched_caps = caps.intersection(req_set)
            match_ratio = len(matched_caps) / max(1, len(req_set)) if req_set else 1.0

            score = round(role.confidence * (0.4 + 0.6 * match_ratio), 4)
            recommendations.append({
                "role_id": role.entity_id,
                "name": role.name,
                "capabilities": list(caps),
                "matched_capabilities": list(matched_caps),
                "score": score,
                "confidence": role.confidence,
            })

        recommendations.sort(key=lambda r: r["score"], reverse=True)
        return recommendations[:limit]

    # ------------------------------------------------------------------
    # Execution Pattern Queries (M24 / M25 Integration)
    # ------------------------------------------------------------------

    def find_proven_goal_patterns(
        self,
        goal_domain: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve successful multi-phase mission execution patterns."""
        patterns: list[dict[str, Any]] = []

        query = KnowledgeGraphQuery(
            entity_types=(EntityType.EXECUTION_PATTERN,),
            keyword=goal_domain,
            min_confidence=0.5,
            limit=limit,
        )
        pattern_entities = self.knowledge_graph.query(query)

        for ent in pattern_entities:
            patterns.append({
                "pattern_id": ent.entity_id,
                "name": ent.name,
                "status": ent.properties.get("status", ""),
                "completed_phases": ent.properties.get("completed_phases", []),
                "duration_seconds": ent.properties.get("duration_seconds", 0.0),
                "confidence": ent.confidence,
            })

        return patterns

    # ------------------------------------------------------------------
    # Subgraph Query
    # ------------------------------------------------------------------

    def query_subgraph(
        self,
        root_entity_id: str,
        max_depth: int = 2,
    ) -> KnowledgeGraphSubgraph:
        """Extract a connected neighborhood subgraph around a root entity."""
        return self.knowledge_graph.traverse_subgraph(
            root_id=root_entity_id,
            max_depth=max_depth,
        )
