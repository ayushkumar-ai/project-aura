"""M56 — PostgreSQL Cognitive Memory Repository Implementation.

Provides production-grade, ACID-compliant persistence for cognitive memories,
contradictions, user profiles, experience patterns, and feedback events on PostgreSQL 16.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.cognitive_memory.contradiction import (
    ContradictionDetector,
    ContradictionResolver,
)
from core.cognitive_memory.lifecycle import MemoryLifecycleManager
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
    UserCognitiveProfile,
)
from core.database import DatabaseConnectionPool
from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository

logger = logging.getLogger("aura.repositories.postgres_cognitive_memory")


def _dt_to_ts(dt: Any) -> float:
    """Helper to convert PostgreSQL TIMESTAMPTZ or float to epoch timestamp."""
    if isinstance(dt, (int, float)):
        return float(dt)
    if isinstance(dt, datetime):
        return dt.timestamp()
    return time.time()


def _row_to_memory(row: dict[str, Any]) -> CognitiveMemory:
    """Helper to map a DB row to a CognitiveMemory instance."""
    source_urls = row.get("source_urls")
    if isinstance(source_urls, str):
        source_urls = json.loads(source_urls)
    elif not isinstance(source_urls, (list, tuple)):
        source_urls = []

    tags = row.get("tags")
    if isinstance(tags, str):
        tags = json.loads(tags)
    elif not isinstance(tags, (list, tuple)):
        tags = []

    structured_data = row.get("structured_data")
    if isinstance(structured_data, str):
        structured_data = json.loads(structured_data)
    elif not isinstance(structured_data, dict):
        structured_data = {}

    metadata = row.get("metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    elif not isinstance(metadata, dict):
        metadata = {}

    embedding = row.get("embedding")
    if isinstance(embedding, str):
        embedding = json.loads(embedding)

    return CognitiveMemory(
        memory_id=str(row["memory_id"]),
        tenant_id=str(row["tenant_id"]),
        memory_type=CognitiveMemoryType(str(row["memory_type"])),
        category=str(row.get("category", "general")),
        key=str(row.get("key", "")),
        content=str(row.get("content", "")),
        structured_data=structured_data,
        confidence=float(row.get("confidence", 1.0)),
        provenance_type=ProvenanceType(str(row.get("provenance_type", "system_derived"))),
        lifecycle_state=LifecycleState(str(row.get("lifecycle_state", "active"))),
        version=int(row.get("version", 1)),
        supersedes_id=str(row["supersedes_id"]) if row.get("supersedes_id") else None,
        taint_status=bool(row.get("taint_status", False)),
        source_urls=tuple(source_urls),
        tags=tuple(tags),
        embedding=embedding if isinstance(embedding, list) else None,
        metadata=metadata,
        access_count=int(row.get("access_count", 0)),
        last_accessed_at=_dt_to_ts(row.get("last_accessed_at")),
        created_at=_dt_to_ts(row.get("created_at")),
        updated_at=_dt_to_ts(row.get("updated_at")),
        expires_at=_dt_to_ts(row.get("expires_at")) if row.get("expires_at") else None,
    )


def _row_to_contradiction(row: dict[str, Any]) -> MemoryContradiction:
    """Helper to map a DB row to a MemoryContradiction instance."""
    res_details = row.get("resolution_details")
    if isinstance(res_details, str):
        res_details = json.loads(res_details)
    elif not isinstance(res_details, dict):
        res_details = {}

    return MemoryContradiction(
        contradiction_id=str(row["contradiction_id"]),
        tenant_id=str(row["tenant_id"]),
        memory_a_id=str(row["memory_a_id"]),
        memory_b_id=str(row["memory_b_id"]),
        contradiction_type=str(row.get("contradiction_type", "fact_conflict")),
        resolution_status=ContradictionStatus(str(row.get("resolution_status", "detected"))),
        resolution_strategy=ResolutionStrategy(str(row.get("resolution_strategy", "provenance_precedence"))),
        resolved_by=str(row["resolved_by"]) if row.get("resolved_by") else None,
        resolution_details=res_details,
        detected_at=_dt_to_ts(row.get("detected_at")),
        resolved_at=_dt_to_ts(row.get("resolved_at")) if row.get("resolved_at") else None,
    )


class PostgresCognitiveMemoryRepository(BaseCognitiveMemoryRepository):
    """PostgreSQL implementation of BaseCognitiveMemoryRepository."""

    def __init__(
        self,
        pool: DatabaseConnectionPool,
        lifecycle_manager: MemoryLifecycleManager | None = None,
        contradiction_detector: ContradictionDetector | None = None,
        contradiction_resolver: ContradictionResolver | None = None,
    ):
        self.pool = pool
        self.lifecycle_manager = lifecycle_manager or MemoryLifecycleManager()
        self.detector = contradiction_detector or ContradictionDetector()
        self.resolver = contradiction_resolver or ContradictionResolver()

    def record_memory(
        self,
        tenant_id: str,
        content: str,
        memory_type: CognitiveMemoryType | str = CognitiveMemoryType.SEMANTIC,
        category: str = "general",
        key: str = "",
        structured_data: dict[str, Any] | None = None,
        confidence: float = 1.0,
        provenance_type: ProvenanceType | str = ProvenanceType.SYSTEM_DERIVED,
        lifecycle_state: LifecycleState | str = LifecycleState.ACTIVE,
        tags: list[str] | tuple[str, ...] | None = None,
        source_urls: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
        auto_resolve_contradictions: bool = True,
    ) -> tuple[CognitiveMemory, MemoryContradiction | None]:
        m_id = memory_id or str(uuid4())
        m_type_str = memory_type.value if isinstance(memory_type, CognitiveMemoryType) else str(memory_type)
        p_type_str = provenance_type.value if isinstance(provenance_type, ProvenanceType) else str(provenance_type)
        l_state_str = lifecycle_state.value if isinstance(lifecycle_state, LifecycleState) else str(lifecycle_state)

        candidate = CognitiveMemory(
            memory_id=m_id,
            tenant_id=tenant_id,
            memory_type=CognitiveMemoryType(m_type_str),
            category=category,
            key=key,
            content=content,
            structured_data=structured_data or {},
            confidence=confidence,
            provenance_type=ProvenanceType(p_type_str),
            lifecycle_state=LifecycleState(l_state_str),
            tags=tuple(tags) if tags else (),
            source_urls=tuple(source_urls) if source_urls else (),
            metadata=metadata or {},
        )

        with self.pool.connection() as conn:
            with conn.transaction():
                # Check for existing active memory conflicts (Invariant M56-F11)
                contradiction_record: MemoryContradiction | None = None
                if auto_resolve_contradictions and candidate.lifecycle_state == LifecycleState.ACTIVE:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT * FROM cognitive_memories
                            WHERE tenant_id = %s AND lifecycle_state = 'active'
                            FOR UPDATE;
                            """,
                            (tenant_id,),
                        )
                        rows = cur.fetchall()
                        existing_mems = [_row_to_memory(r) for r in rows]

                    conflicts = self.detector.detect_conflicts(candidate, existing_mems)
                    if conflicts:
                        existing_clash, reason = conflicts[0]
                        retained, superseded, contra = self.resolver.resolve(existing_clash, candidate)
                        
                        # Apply resolution updates in DB (Invariant M56-F06, M56-F12)
                        with conn.cursor() as cur:
                            # 1. Update loser/superseded memory
                            cur.execute(
                                """
                                UPDATE cognitive_memories
                                SET lifecycle_state = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE memory_id = %s AND tenant_id = %s;
                                """,
                                (superseded.lifecycle_state.value, superseded.memory_id, tenant_id),
                            )

                            # 2. Insert or update winner memory
                            cur.execute(
                                """
                                INSERT INTO cognitive_memories (
                                    memory_id, tenant_id, memory_type, category, key, content,
                                    structured_data, confidence, provenance_type, lifecycle_state,
                                    version, supersedes_id, taint_status, source_urls, tags,
                                    metadata, access_count, created_at, updated_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                                ) ON CONFLICT (memory_id) DO UPDATE SET
                                    lifecycle_state = EXCLUDED.lifecycle_state,
                                    confidence = EXCLUDED.confidence,
                                    version = EXCLUDED.version,
                                    supersedes_id = EXCLUDED.supersedes_id,
                                    updated_at = CURRENT_TIMESTAMP;
                                """,
                                (
                                    retained.memory_id, retained.tenant_id, retained.memory_type.value,
                                    retained.category, retained.key, retained.content,
                                    json.dumps(retained.structured_data), retained.confidence,
                                    retained.provenance_type.value, retained.lifecycle_state.value,
                                    retained.version, retained.supersedes_id, retained.taint_status,
                                    json.dumps(list(retained.source_urls)), json.dumps(list(retained.tags)),
                                    json.dumps(retained.metadata), retained.access_count,
                                ),
                            )

                            # 3. Insert contradiction record
                            cur.execute(
                                """
                                INSERT INTO memory_contradictions (
                                    contradiction_id, tenant_id, memory_a_id, memory_b_id,
                                    contradiction_type, resolution_status, resolution_strategy,
                                    resolved_by, resolution_details, detected_at, resolved_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                                """,
                                (
                                    contra.contradiction_id, contra.tenant_id, contra.memory_a_id, contra.memory_b_id,
                                    contra.contradiction_type, contra.resolution_status.value, contra.resolution_strategy.value,
                                    contra.resolved_by, json.dumps(contra.resolution_details),
                                ),
                            )
                        return retained, contra

                # Standard Insert
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cognitive_memories (
                            memory_id, tenant_id, memory_type, category, key, content,
                            structured_data, confidence, provenance_type, lifecycle_state,
                            version, supersedes_id, taint_status, source_urls, tags,
                            metadata, access_count, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        );
                        """,
                        (
                            candidate.memory_id, candidate.tenant_id, candidate.memory_type.value,
                            candidate.category, candidate.key, candidate.content,
                            json.dumps(candidate.structured_data), candidate.confidence,
                            candidate.provenance_type.value, candidate.lifecycle_state.value,
                            candidate.version, candidate.supersedes_id, candidate.taint_status,
                            json.dumps(list(candidate.source_urls)), json.dumps(list(candidate.tags)),
                            json.dumps(candidate.metadata), candidate.access_count,
                        ),
                    )

        return candidate, None

    def get_memory(self, memory_id: str, tenant_id: str) -> CognitiveMemory | None:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE cognitive_memories
                        SET access_count = access_count + 1, last_accessed_at = CURRENT_TIMESTAMP
                        WHERE memory_id = %s AND tenant_id = %s
                        RETURNING *;
                        """,
                        (memory_id, tenant_id),
                    )
                    row = cur.fetchone()
                    if row:
                        return _row_to_memory(row)
        return None

    def query_memories(
        self,
        tenant_id: str,
        memory_type: CognitiveMemoryType | str | None = None,
        category: str | None = None,
        key: str | None = None,
        lifecycle_state: LifecycleState | str | None = LifecycleState.ACTIVE,
        min_confidence: float = 0.0,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[CognitiveMemory]:
        clauses = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]

        if memory_type:
            m_val = memory_type.value if isinstance(memory_type, CognitiveMemoryType) else str(memory_type)
            clauses.append("memory_type = %s")
            params.append(m_val)

        if category:
            clauses.append("category = %s")
            params.append(category)

        if key:
            clauses.append("key = %s")
            params.append(key)

        if lifecycle_state:
            l_val = lifecycle_state.value if isinstance(lifecycle_state, LifecycleState) else str(lifecycle_state)
            clauses.append("lifecycle_state = %s")
            params.append(l_val)

        if min_confidence > 0.0:
            clauses.append("confidence >= %s")
            params.append(min_confidence)

        if query and query.strip():
            clauses.append("(content ILIKE %s OR key ILIKE %s)")
            params.extend([f"%{query}%", f"%{query}%"])

        params.extend([limit, offset])

        sql = f"""
            SELECT * FROM cognitive_memories
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s;
        """

        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [_row_to_memory(r) for r in rows]

    def update_memory_state(
        self,
        memory_id: str,
        tenant_id: str,
        lifecycle_state: LifecycleState | str,
        confidence: float | None = None,
        reason: str = "",
    ) -> CognitiveMemory | None:
        l_val = lifecycle_state.value if isinstance(lifecycle_state, LifecycleState) else str(lifecycle_state)
        
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if confidence is not None:
                        conf_val = max(0.0, min(1.0, float(confidence)))
                        cur.execute(
                            """
                            UPDATE cognitive_memories
                            SET lifecycle_state = %s, confidence = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE memory_id = %s AND tenant_id = %s
                            RETURNING *;
                            """,
                            (l_val, conf_val, memory_id, tenant_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE cognitive_memories
                            SET lifecycle_state = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE memory_id = %s AND tenant_id = %s
                            RETURNING *;
                            """,
                            (l_val, memory_id, tenant_id),
                        )
                    row = cur.fetchone()
                    if row:
                        return _row_to_memory(row)
        return None

    def delete_memory(
        self,
        memory_id: str,
        tenant_id: str,
        hard_delete: bool = False,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if hard_delete:
                        cur.execute(
                            "DELETE FROM cognitive_memories WHERE memory_id = %s AND tenant_id = %s;",
                            (memory_id, tenant_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE cognitive_memories
                            SET lifecycle_state = 'deleted', updated_at = CURRENT_TIMESTAMP
                            WHERE memory_id = %s AND tenant_id = %s;
                            """,
                            (memory_id, tenant_id),
                        )
                    return cur.rowcount > 0

    def clear_tenant_memories(self, tenant_id: str) -> int:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memory_feedback_events WHERE tenant_id = %s;", (tenant_id,))
                    cur.execute("DELETE FROM memory_contradictions WHERE tenant_id = %s;", (tenant_id,))
                    cur.execute("DELETE FROM cognitive_memories WHERE tenant_id = %s;", (tenant_id,))
                    deleted_mems = cur.rowcount
                    cur.execute("DELETE FROM user_cognitive_profiles WHERE tenant_id = %s;", (tenant_id,))
                    cur.execute("DELETE FROM experience_patterns WHERE tenant_id = %s;", (tenant_id,))
                    return deleted_mems

    def get_profile(self, tenant_id: str) -> UserCognitiveProfile | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM user_cognitive_profiles WHERE tenant_id = %s;", (tenant_id,))
                row = cur.fetchone()
                if row:
                    prefs = row["preferences"]
                    if isinstance(prefs, str):
                        prefs = json.loads(prefs)
                    traits = row["inferred_traits"]
                    if isinstance(traits, str):
                        traits = json.loads(traits)
                    metrics = row["interaction_metrics"]
                    if isinstance(metrics, str):
                        metrics = json.loads(metrics)

                    return UserCognitiveProfile(
                        profile_id=str(row["profile_id"]),
                        tenant_id=str(row["tenant_id"]),
                        preferences=prefs if isinstance(prefs, dict) else {},
                        inferred_traits=traits if isinstance(traits, dict) else {},
                        interaction_metrics=metrics if isinstance(metrics, dict) else {},
                        version=int(row.get("version", 1)),
                        created_at=_dt_to_ts(row.get("created_at")),
                        updated_at=_dt_to_ts(row.get("updated_at")),
                    )
        return None

    def save_profile(self, profile: UserCognitiveProfile) -> UserCognitiveProfile:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO user_cognitive_profiles (
                            profile_id, tenant_id, preferences, inferred_traits,
                            interaction_metrics, version, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        ) ON CONFLICT (tenant_id) DO UPDATE SET
                            preferences = EXCLUDED.preferences,
                            inferred_traits = EXCLUDED.inferred_traits,
                            interaction_metrics = EXCLUDED.interaction_metrics,
                            version = user_cognitive_profiles.version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *;
                        """,
                        (
                            profile.profile_id, profile.tenant_id,
                            json.dumps(profile.preferences), json.dumps(profile.inferred_traits),
                            json.dumps(profile.interaction_metrics), profile.version,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        return self.get_profile(profile.tenant_id) or profile
        return profile

    def get_experience_pattern(self, tenant_id: str, context_key: str) -> ExperiencePattern | None:
        clean_key = context_key.strip().lower()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM experience_patterns WHERE tenant_id = %s AND context_key = %s;",
                    (tenant_id, clean_key),
                )
                row = cur.fetchone()
                if row:
                    tools = row.get("optimal_tools", [])
                    if isinstance(tools, str):
                        tools = json.loads(tools)
                    modes = row.get("failure_modes", [])
                    if isinstance(modes, str):
                        modes = json.loads(modes)
                    recs = row.get("recommendations", [])
                    if isinstance(recs, str):
                        recs = json.loads(recs)

                    return ExperiencePattern(
                        pattern_id=str(row["pattern_id"]),
                        tenant_id=str(row["tenant_id"]),
                        context_key=str(row["context_key"]),
                        success_count=int(row.get("success_count", 0)),
                        failure_count=int(row.get("failure_count", 0)),
                        average_latency_ms=float(row.get("average_latency_ms", 0.0)),
                        optimal_tools=list(tools) if isinstance(tools, list) else [],
                        failure_modes=list(modes) if isinstance(modes, list) else [],
                        recommendations=list(recs) if isinstance(recs, list) else [],
                        confidence=float(row.get("confidence", 1.0)),
                        created_at=_dt_to_ts(row.get("created_at")),
                        updated_at=_dt_to_ts(row.get("updated_at")),
                    )
        return None

    def save_experience_pattern(self, pattern: ExperiencePattern) -> ExperiencePattern:
        clean_key = pattern.context_key.strip().lower()
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO experience_patterns (
                            pattern_id, tenant_id, context_key, success_count, failure_count,
                            average_latency_ms, optimal_tools, failure_modes, recommendations,
                            confidence, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        ) ON CONFLICT (tenant_id, context_key) DO UPDATE SET
                            success_count = EXCLUDED.success_count,
                            failure_count = EXCLUDED.failure_count,
                            average_latency_ms = EXCLUDED.average_latency_ms,
                            optimal_tools = EXCLUDED.optimal_tools,
                            failure_modes = EXCLUDED.failure_modes,
                            recommendations = EXCLUDED.recommendations,
                            confidence = EXCLUDED.confidence,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *;
                        """,
                        (
                            pattern.pattern_id, pattern.tenant_id, clean_key,
                            pattern.success_count, pattern.failure_count,
                            pattern.average_latency_ms, json.dumps(pattern.optimal_tools),
                            json.dumps(pattern.failure_modes), json.dumps(pattern.recommendations),
                            pattern.confidence,
                        ),
                    )
        return self.get_experience_pattern(pattern.tenant_id, clean_key) or pattern

    def list_experience_patterns(self, tenant_id: str, limit: int = 50) -> list[ExperiencePattern]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM experience_patterns WHERE tenant_id = %s ORDER BY updated_at DESC LIMIT %s;",
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
                results: list[ExperiencePattern] = []
                for row in rows:
                    tools = row.get("optimal_tools", [])
                    if isinstance(tools, str):
                        tools = json.loads(tools)
                    modes = row.get("failure_modes", [])
                    if isinstance(modes, str):
                        modes = json.loads(modes)
                    recs = row.get("recommendations", [])
                    if isinstance(recs, str):
                        recs = json.loads(recs)

                    results.append(
                        ExperiencePattern(
                            pattern_id=str(row["pattern_id"]),
                            tenant_id=str(row["tenant_id"]),
                            context_key=str(row["context_key"]),
                            success_count=int(row.get("success_count", 0)),
                            failure_count=int(row.get("failure_count", 0)),
                            average_latency_ms=float(row.get("average_latency_ms", 0.0)),
                            optimal_tools=list(tools) if isinstance(tools, list) else [],
                            failure_modes=list(modes) if isinstance(modes, list) else [],
                            recommendations=list(recs) if isinstance(recs, list) else [],
                            confidence=float(row.get("confidence", 1.0)),
                            created_at=_dt_to_ts(row.get("created_at")),
                            updated_at=_dt_to_ts(row.get("updated_at")),
                        )
                    )
                return results

    def record_feedback(self, feedback: MemoryFeedbackEvent) -> MemoryFeedbackEvent:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO memory_feedback_events (
                            event_id, tenant_id, target_memory_id, feedback_type,
                            correction_content, metadata, applied, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
                        """,
                        (
                            feedback.event_id, feedback.tenant_id, feedback.target_memory_id,
                            feedback.feedback_type.value, feedback.correction_content,
                            json.dumps(feedback.metadata), feedback.applied,
                        ),
                    )
        return feedback

    def list_contradictions(
        self,
        tenant_id: str,
        status: ContradictionStatus | str | None = None,
        limit: int = 50,
    ) -> list[MemoryContradiction]:
        clauses = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]

        if status:
            st_val = status.value if isinstance(status, ContradictionStatus) else str(status)
            clauses.append("resolution_status = %s")
            params.append(st_val)

        params.append(limit)
        sql = f"""
            SELECT * FROM memory_contradictions
            WHERE {' AND '.join(clauses)}
            ORDER BY detected_at DESC
            LIMIT %s;
        """

        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [_row_to_contradiction(r) for r in rows]

    def resolve_contradiction(
        self,
        contradiction_id: str,
        tenant_id: str,
        resolution_strategy: ResolutionStrategy | str,
        resolved_by: str,
        winning_memory_id: str,
    ) -> bool:
        strat_val = resolution_strategy.value if isinstance(resolution_strategy, ResolutionStrategy) else str(resolution_strategy)
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM memory_contradictions WHERE contradiction_id = %s AND tenant_id = %s FOR UPDATE;",
                        (contradiction_id, tenant_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return False

                    contra = _row_to_contradiction(row)
                    loser_id = contra.memory_b_id if winning_memory_id == contra.memory_a_id else contra.memory_a_id

                    cur.execute(
                        """
                        UPDATE memory_contradictions
                        SET resolution_status = 'resolved', resolution_strategy = %s,
                            resolved_by = %s, resolution_details = %s, resolved_at = CURRENT_TIMESTAMP
                        WHERE contradiction_id = %s AND tenant_id = %s;
                        """,
                        (strat_val, resolved_by, json.dumps({"manual_winner": winning_memory_id, "resolved_by": resolved_by}), contradiction_id, tenant_id),
                    )

                    cur.execute(
                        "UPDATE cognitive_memories SET lifecycle_state = 'active', updated_at = CURRENT_TIMESTAMP WHERE memory_id = %s AND tenant_id = %s;",
                        (winning_memory_id, tenant_id),
                    )
                    cur.execute(
                        "UPDATE cognitive_memories SET lifecycle_state = 'superseded', updated_at = CURRENT_TIMESTAMP WHERE memory_id = %s AND tenant_id = %s;",
                        (loser_id, tenant_id),
                    )
                    return True
