"""Milestone 28: Epistemic Knowledge Graph Store & Indexed Graph Engine.

Provides a thread-safe, high-performance, indexed property graph for storing,
traversing, and querying knowledge entities and directed relations across
mission campaigns, multi-agent collaborations, synthesized skills, and fault remediations.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any
from uuid import uuid4

from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeGraphQuery,
    KnowledgeGraphSubgraph,
    KnowledgeRelation,
    RelationType,
)

logger = logging.getLogger("aura.epistemic_graph")

DEFAULT_MAX_ENTITIES = 10000
DEFAULT_MAX_RELATIONS = 50000


class EpistemicKnowledgeGraph:
    """Thread-safe, indexed Epistemic Knowledge Graph store."""

    def __init__(
        self,
        max_entities: int = DEFAULT_MAX_ENTITIES,
        max_relations: int = DEFAULT_MAX_RELATIONS,
    ) -> None:
        self.max_entities = max(1, int(max_entities))
        self.max_relations = max(1, int(max_relations))
        self._lock = threading.RLock()

        # Primary storage
        self._entities: dict[str, KnowledgeEntity] = {}
        self._relations: dict[str, KnowledgeRelation] = {}

        # Adjacency and fast indexing
        self._outgoing: dict[str, set[str]] = defaultdict(set)  # source_id -> set of relation_ids
        self._incoming: dict[str, set[str]] = defaultdict(set)  # target_id -> set of relation_ids
        self._by_type: dict[EntityType, set[str]] = defaultdict(set)  # EntityType -> set of entity_ids
        self._capability_index: dict[str, set[str]] = defaultdict(set)  # keyword -> set of entity_ids

    # ------------------------------------------------------------------
    # Entity Operations
    # ------------------------------------------------------------------

    def add_entity(self, entity: KnowledgeEntity, overwrite: bool = True) -> KnowledgeEntity:
        """Add or update an entity in the graph.

        Enforces bounded node capacity using LRU/confidence pruning.
        """
        if not isinstance(entity, KnowledgeEntity):
            raise TypeError("entity must be a KnowledgeEntity instance.")

        with self._lock:
            eid = entity.entity_id
            if eid in self._entities and not overwrite:
                return self._entities[eid]

            # Capacity guard
            if len(self._entities) >= self.max_entities and eid not in self._entities:
                self._prune_entities(target_count=self.max_entities - 1)

            # If replacing an existing entity, clean up old index entries
            if eid in self._entities:
                old_entity = self._entities[eid]
                self._by_type[old_entity.entity_type].discard(eid)
                self._remove_from_capability_index(old_entity)

            # Store and index
            self._entities[eid] = entity
            self._by_type[entity.entity_type].add(eid)
            self._index_entity_capabilities(entity)

            logger.debug("EpistemicGraph: added entity '%s' (%s)", eid, entity.entity_type.value)
            return entity

    def get_entity(self, entity_id: str, record_access: bool = True) -> KnowledgeEntity | None:
        """Retrieve an entity by ID, optionally updating access metrics."""
        with self._lock:
            eid = str(entity_id).strip()
            entity = self._entities.get(eid)
            if entity is None:
                return None
            if record_access:
                updated = entity.with_access()
                self._entities[eid] = updated
                return updated
            return entity

    def has_entity(self, entity_id: str) -> bool:
        """Check if an entity exists in the graph."""
        with self._lock:
            return str(entity_id).strip() in self._entities

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity and all its connected incident relations."""
        with self._lock:
            eid = str(entity_id).strip()
            entity = self._entities.pop(eid, None)
            if entity is None:
                return False

            self._by_type[entity.entity_type].discard(eid)
            self._remove_from_capability_index(entity)

            # Remove all outgoing relations
            for rel_id in list(self._outgoing.get(eid, set())):
                self.remove_relation(rel_id)

            # Remove all incoming relations
            for rel_id in list(self._incoming.get(eid, set())):
                self.remove_relation(rel_id)

            self._outgoing.pop(eid, None)
            self._incoming.pop(eid, None)
            return True

    # ------------------------------------------------------------------
    # Relation Operations
    # ------------------------------------------------------------------

    def add_relation(
        self,
        relation: KnowledgeRelation,
        reinforce_if_exists: bool = True,
    ) -> KnowledgeRelation:
        """Add a directed relation between two existing entities."""
        if not isinstance(relation, KnowledgeRelation):
            raise TypeError("relation must be a KnowledgeRelation instance.")

        with self._lock:
            # Validate endpoints exist
            if relation.source_id not in self._entities or relation.target_id not in self._entities:
                raise ValueError(
                    f"Cannot add relation '{relation.relation_id}': endpoints "
                    f"({relation.source_id} -> {relation.target_id}) must exist in graph."
                )

            # Check if an existing relation with same endpoints and type exists
            existing_id = self._find_relation_id(
                relation.source_id, relation.target_id, relation.relation_type
            )
            if existing_id and reinforce_if_exists:
                existing = self._relations[existing_id]
                reinforced = existing.with_reinforcement(weight_delta=0.1, confidence_boost=0.05)
                self._relations[existing_id] = reinforced
                return reinforced

            # Capacity guard
            if len(self._relations) >= self.max_relations and relation.relation_id not in self._relations:
                self._prune_relations(target_count=self.max_relations - 1)

            rid = relation.relation_id
            self._relations[rid] = relation
            self._outgoing[relation.source_id].add(rid)
            self._incoming[relation.target_id].add(rid)

            logger.debug(
                "EpistemicGraph: added relation '%s' (%s -%s-> %s)",
                rid, relation.source_id, relation.relation_type.value, relation.target_id,
            )
            return relation

    def get_relation(self, relation_id: str) -> KnowledgeRelation | None:
        """Retrieve a relation by ID."""
        with self._lock:
            return self._relations.get(str(relation_id).strip())

    def remove_relation(self, relation_id: str) -> bool:
        """Remove a relation from the graph."""
        with self._lock:
            rid = str(relation_id).strip()
            rel = self._relations.pop(rid, None)
            if rel is None:
                return False

            self._outgoing[rel.source_id].discard(rid)
            self._incoming[rel.target_id].discard(rid)
            return True

    # ------------------------------------------------------------------
    # Graph Traversal & Query Operations
    # ------------------------------------------------------------------

    def get_outgoing_relations(
        self,
        source_id: str,
        relation_type: RelationType | None = None,
    ) -> list[KnowledgeRelation]:
        """Retrieve all outgoing relations from an entity."""
        with self._lock:
            rel_ids = self._outgoing.get(str(source_id).strip(), set())
            rels = [self._relations[rid] for rid in rel_ids if rid in self._relations]
            if relation_type is not None:
                rels = [r for r in rels if r.relation_type == relation_type]
            return rels

    def get_incoming_relations(
        self,
        target_id: str,
        relation_type: RelationType | None = None,
    ) -> list[KnowledgeRelation]:
        """Retrieve all incoming relations to an entity."""
        with self._lock:
            rel_ids = self._incoming.get(str(target_id).strip(), set())
            rels = [self._relations[rid] for rid in rel_ids if rid in self._relations]
            if relation_type is not None:
                rels = [r for r in rels if r.relation_type == relation_type]
            return rels

    def get_neighbors(
        self,
        entity_id: str,
        relation_type: RelationType | None = None,
        direction: str = "outgoing",
    ) -> list[KnowledgeEntity]:
        """Retrieve neighboring entities connected by directed relations."""
        with self._lock:
            eid = str(entity_id).strip()
            target_ids: set[str] = set()

            if direction in ("outgoing", "both"):
                for rel in self.get_outgoing_relations(eid, relation_type):
                    target_ids.add(rel.target_id)

            if direction in ("incoming", "both"):
                for rel in self.get_incoming_relations(eid, relation_type):
                    target_ids.add(rel.source_id)

            return [self._entities[tid] for tid in target_ids if tid in self._entities]

    def traverse_subgraph(
        self,
        root_id: str,
        max_depth: int = 2,
        relation_types: tuple[RelationType, ...] = (),
        min_confidence: float = 0.0,
    ) -> KnowledgeGraphSubgraph:
        """Perform a multi-hop BFS traversal from root_id up to max_depth."""
        with self._lock:
            rid = str(root_id).strip()
            if rid not in self._entities:
                return KnowledgeGraphSubgraph(entities=(), relations=(), root_entity_id=rid)

            visited_entities: dict[str, KnowledgeEntity] = {rid: self._entities[rid]}
            visited_relations: dict[str, KnowledgeRelation] = {}
            queue: deque[tuple[str, int]] = deque([(rid, 0)])

            rel_filter = set(relation_types) if relation_types else None

            while queue:
                curr_id, depth = queue.popleft()
                if depth >= max_depth:
                    continue

                for rel_id in self._outgoing.get(curr_id, set()):
                    rel = self._relations.get(rel_id)
                    if rel is None or rel.confidence < min_confidence:
                        continue
                    if rel_filter and rel.relation_type not in rel_filter:
                        continue

                    visited_relations[rel.relation_id] = rel
                    neighbor_id = rel.target_id
                    if neighbor_id not in visited_entities and neighbor_id in self._entities:
                        visited_entities[neighbor_id] = self._entities[neighbor_id]
                        queue.append((neighbor_id, depth + 1))

            return KnowledgeGraphSubgraph(
                entities=tuple(visited_entities.values()),
                relations=tuple(visited_relations.values()),
                root_entity_id=rid,
                extracted_at=time.time(),
            )

    def query(self, query: KnowledgeGraphQuery) -> list[KnowledgeEntity]:
        """Execute a multi-criteria search query over the graph."""
        with self._lock:
            # Start with candidate IDs based on EntityType filter
            if query.entity_types:
                candidate_ids: set[str] = set()
                for et in query.entity_types:
                    candidate_ids.update(self._by_type.get(et, set()))
            else:
                candidate_ids = set(self._entities.keys())

            # Keyword filter
            if query.keyword:
                kw = query.keyword.lower()
                matching_ids = set()
                # Check capability index
                for cap_k, eids in self._capability_index.items():
                    if kw in cap_k:
                        matching_ids.update(eids)

                # Check entity name and properties
                for eid in candidate_ids:
                    ent = self._entities[eid]
                    if kw in ent.name.lower() or kw in str(ent.properties).lower():
                        matching_ids.add(eid)

                candidate_ids.intersection_update(matching_ids)

            # Confidence filter
            results: list[KnowledgeEntity] = []
            for eid in candidate_ids:
                ent = self._entities.get(eid)
                if ent and ent.confidence >= query.min_confidence:
                    results.append(ent)

            # Sort by confidence descending, then access_count descending
            results.sort(key=lambda e: (e.confidence, e.access_count), reverse=True)
            return results[:query.limit]

    def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 4,
    ) -> list[KnowledgeEntity]:
        """Find the shortest directed path between two entities using BFS."""
        with self._lock:
            sid = str(start_id).strip()
            eid = str(end_id).strip()
            if sid not in self._entities or eid not in self._entities:
                return []
            if sid == eid:
                return [self._entities[sid]]

            queue: deque[tuple[str, list[str]]] = deque([(sid, [sid])])
            visited: set[str] = {sid}

            while queue:
                curr_id, path = queue.popleft()
                if len(path) > max_depth:
                    continue

                for rel_id in self._outgoing.get(curr_id, set()):
                    rel = self._relations.get(rel_id)
                    if rel is None:
                        continue
                    nxt = rel.target_id
                    if nxt == eid:
                        full_path = path + [nxt]
                        return [self._entities[pid] for pid in full_path if pid in self._entities]
                    if nxt not in visited and nxt in self._entities:
                        visited.add(nxt)
                        queue.append((nxt, path + [nxt]))

            return []

    def list_entities(
        self,
        entity_type: EntityType | None = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        """List entities with optional type and confidence filtering."""
        with self._lock:
            if entity_type is not None:
                eids = self._by_type.get(entity_type, set())
            else:
                eids = set(self._entities.keys())

            results = [
                self._entities[eid] for eid in eids
                if eid in self._entities and self._entities[eid].confidence >= min_confidence
            ]
            results.sort(key=lambda e: e.created_at, reverse=True)
            return results[:limit]

    def list_relations(
        self,
        relation_type: RelationType | None = None,
        limit: int = 100,
    ) -> list[KnowledgeRelation]:
        """List relations with optional type filtering."""
        with self._lock:
            rels = list(self._relations.values())
            if relation_type is not None:
                rels = [r for r in rels if r.relation_type == relation_type]
            rels.sort(key=lambda r: (r.weight, r.confidence), reverse=True)
            return rels[:limit]

    # ------------------------------------------------------------------
    # Pruning & Memory Bounds
    # ------------------------------------------------------------------

    def _prune_entities(self, target_count: int) -> int:
        """Evict lowest utility entities (low confidence + oldest access)."""
        if len(self._entities) <= target_count:
            return 0

        # Sort entities by (confidence ASC, last_accessed_at ASC)
        sorted_eids = sorted(
            self._entities.keys(),
            key=lambda eid: (self._entities[eid].confidence, self._entities[eid].last_accessed_at),
        )

        num_to_remove = len(self._entities) - target_count
        removed = 0
        for eid in sorted_eids[:num_to_remove]:
            if self.remove_entity(eid):
                removed += 1

        logger.info("EpistemicGraph: pruned %d entities to stay under limit.", removed)
        return removed

    def _prune_relations(self, target_count: int) -> int:
        """Evict lowest utility relations (low weight + low confidence)."""
        if len(self._relations) <= target_count:
            return 0

        sorted_rids = sorted(
            self._relations.keys(),
            key=lambda rid: (self._relations[rid].weight, self._relations[rid].confidence),
        )

        num_to_remove = len(self._relations) - target_count
        removed = 0
        for rid in sorted_rids[:num_to_remove]:
            if self.remove_relation(rid):
                removed += 1

        logger.info("EpistemicGraph: pruned %d relations to stay under limit.", removed)
        return removed

    # ------------------------------------------------------------------
    # Checkpoint Serialization & Recovery
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize complete graph state to dictionary for checkpoint persistence."""
        with self._lock:
            return {
                "max_entities": self.max_entities,
                "max_relations": self.max_relations,
                "entities": [e.to_dict() for e in self._entities.values()],
                "relations": [r.to_dict() for r in self._relations.values()],
            }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        max_entities: int = DEFAULT_MAX_ENTITIES,
        max_relations: int = DEFAULT_MAX_RELATIONS,
    ) -> "EpistemicKnowledgeGraph":
        """Restore graph from checkpoint payload."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")

        graph = cls(
            max_entities=data.get("max_entities", max_entities),
            max_relations=data.get("max_relations", max_relations),
        )

        # Restore entities first
        for ed in data.get("entities", []):
            try:
                entity = KnowledgeEntity.from_dict(ed)
                graph.add_entity(entity, overwrite=True)
            except Exception as ex:
                logger.debug("EpistemicGraph: skipping corrupt entity during restore: %s", ex)

        # Restore relations
        for rd in data.get("relations", []):
            try:
                relation = KnowledgeRelation.from_dict(rd)
                if graph.has_entity(relation.source_id) and graph.has_entity(relation.target_id):
                    graph.add_relation(relation, reinforce_if_exists=False)
            except Exception as ex:
                logger.debug("EpistemicGraph: skipping corrupt relation during restore: %s", ex)

        return graph

    # ------------------------------------------------------------------
    # Internal Indexing Helpers
    # ------------------------------------------------------------------

    def _find_relation_id(self, source_id: str, target_id: str, relation_type: RelationType) -> str | None:
        for rid in self._outgoing.get(source_id, set()):
            rel = self._relations.get(rid)
            if rel and rel.target_id == target_id and rel.relation_type == relation_type:
                return rid
        return None

    def _index_entity_capabilities(self, entity: KnowledgeEntity) -> None:
        caps = entity.properties.get("capabilities", [])
        if isinstance(caps, (list, tuple, set)):
            for cap in caps:
                k = str(cap).strip().lower()
                if k:
                    self._capability_index[k].add(entity.entity_id)

    def _remove_from_capability_index(self, entity: KnowledgeEntity) -> None:
        caps = entity.properties.get("capabilities", [])
        if isinstance(caps, (list, tuple, set)):
            for cap in caps:
                k = str(cap).strip().lower()
                if k in self._capability_index:
                    self._capability_index[k].discard(entity.entity_id)
                    if not self._capability_index[k]:
                        self._capability_index.pop(k, None)
