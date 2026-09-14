"""M42 — PostgreSQL Production Repository Implementations for Project AURA.

Implements all M42 repository contracts backed by authoritative PostgreSQL storage,
using DatabaseConnectionPool, parameterized SQL queries, JSONB serialization,
and composite foreign keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from uuid import uuid4

from core.database import DatabaseConnectionPool
from core.identity import UserIdentity, UserRole, UserScope
from core.personal_state_types import (
    EpisodicExperienceRecord,
    MemoryCategory,
    UnifiedMemoryRecord,
    UserPreferences,
)
from core.repositories.base import (
    BaseApiTokenRepository,
    BaseCheckpointRepository,
    BaseConversationRepository,
    BaseExperienceRepository,
    BaseMemoryRepository,
    BaseUserPreferencesRepository,
    BaseUserRepository,
)

logger = logging.getLogger("aura.repositories.postgres")


def _hash_token(raw_token: str) -> str:
    """Compute SHA256 hex digest for token secret."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


class PostgresUserRepository(BaseUserRepository):
    """PostgreSQL user repository."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def get_by_id(self, user_id: str) -> UserIdentity | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, username, role, is_active, EXTRACT(EPOCH FROM created_at) as created_at
                    FROM users WHERE id = %s;
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                role = UserRole(row["role"]) if row["role"] in UserRole.__members__.values() else UserRole.USER
                return UserIdentity(
                    user_id=row["id"],
                    username=row["username"],
                    roles=frozenset({role}),
                    is_authenticated=row["is_active"],
                    created_at=row["created_at"] or time.time(),
                )

    def get_by_username(self, username: str) -> UserIdentity | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, username, role, is_active, EXTRACT(EPOCH FROM created_at) as created_at
                    FROM users WHERE LOWER(username) = LOWER(%s);
                    """,
                    (username.strip(),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                role = UserRole(row["role"]) if row["role"] in UserRole.__members__.values() else UserRole.USER
                return UserIdentity(
                    user_id=row["id"],
                    username=row["username"],
                    roles=frozenset({role}),
                    is_authenticated=row["is_active"],
                    created_at=row["created_at"] or time.time(),
                )

    def save(self, user: UserIdentity) -> UserIdentity:
        role_val = next(iter(user.roles)).value if user.roles else UserRole.USER.value
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE
                    SET username = EXCLUDED.username,
                        role = EXCLUDED.role,
                        is_active = EXCLUDED.is_active,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (user.user_id, user.username, role_val, user.is_authenticated),
                )
        return user

    def delete(self, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s;", (user_id,))
                return cur.rowcount > 0

    def list_users(self, limit: int = 100, offset: int = 0) -> list[UserIdentity]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, username, role, is_active, EXTRACT(EPOCH FROM created_at) as created_at
                    FROM users ORDER BY created_at ASC LIMIT %s OFFSET %s;
                    """,
                    (limit, offset),
                )
                users = []
                for row in cur.fetchall():
                    role = UserRole(row["role"]) if row["role"] in UserRole.__members__.values() else UserRole.USER
                    users.append(
                        UserIdentity(
                            user_id=row["id"],
                            username=row["username"],
                            roles=frozenset({role}),
                            is_authenticated=row["is_active"],
                            created_at=row["created_at"] or time.time(),
                        )
                    )
                return users


class PostgresUserPreferencesRepository(BaseUserPreferencesRepository):
    """PostgreSQL user preferences repository."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def get(self, user_id: str) -> UserPreferences:
        uid = user_id if (user_id and str(user_id).strip()) else "default"
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT preferences FROM user_preferences WHERE user_id = %s;",
                    (uid,),
                )
                row = cur.fetchone()
                if not row or not row.get("preferences"):
                    return UserPreferences(user_id=uid)
                prefs_data = row["preferences"]
                if isinstance(prefs_data, str):
                    prefs_data = json.loads(prefs_data)
                prefs_data["user_id"] = uid
                return UserPreferences.from_dict(prefs_data)

    def save(self, user_id: str, preferences: UserPreferences | dict[str, Any]) -> UserPreferences:
        uid = user_id if (user_id and str(user_id).strip()) else "default"
        if isinstance(preferences, dict):
            current = self.get(uid).to_dict()
            current.update(preferences)
            current["user_id"] = uid
            current["updated_at"] = time.time()
            updated = UserPreferences.from_dict(current)
        elif isinstance(preferences, UserPreferences):
            preferences.user_id = uid
            preferences.updated_at = time.time()
            updated = preferences
        else:
            raise TypeError("preferences must be UserPreferences or dict")

        payload = json.dumps(updated.to_dict())
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Ensure user exists before inserting preferences if foreign key is enforced
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active)
                    VALUES (%s, %s, 'user', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (uid, uid),
                )
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, preferences, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO UPDATE
                    SET preferences = EXCLUDED.preferences,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (uid, payload),
                )
        return updated

    def delete(self, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_preferences WHERE user_id = %s;", (user_id,))
                return cur.rowcount > 0


class PostgresApiTokenRepository(BaseApiTokenRepository):
    """PostgreSQL secure API token repository with SHA256 hashing."""

    def __init__(self, pool: DatabaseConnectionPool, user_repo: BaseUserRepository) -> None:
        self.pool = pool
        self.user_repo = user_repo

    def register_token(
        self,
        raw_token: str,
        user_id: str,
        expires_in_seconds: float = 2592000,
        rate_limit_rpm: int = 60,
        rate_limit_tpm: int = 60000,
        token_id: str | None = None,
    ) -> str:
        if not raw_token or not raw_token.strip():
            raise ValueError("Token cannot be empty.")
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id cannot be empty.")

        tid = token_id or f"tok_{uuid4().hex[:12]}"
        thash = _hash_token(raw_token)
        prefix = raw_token.strip()[:8] if len(raw_token.strip()) >= 8 else raw_token.strip()
        now = time.time()
        expires_at = now + expires_in_seconds

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Ensure user exists
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active)
                    VALUES (%s, %s, 'user', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (user_id, user_id),
                )
                cur.execute(
                    """
                    INSERT INTO api_tokens (
                        id, user_id, token_hash, prefix, created_at, expires_at,
                        revoked_at, is_active, rate_limit_rpm, rate_limit_tpm
                    ) VALUES (
                        %s, %s, %s, %s, TO_TIMESTAMP(%s), TO_TIMESTAMP(%s),
                        NULL, TRUE, %s, %s
                    );
                    """,
                    (tid, user_id, thash, prefix, now, expires_at, rate_limit_rpm, rate_limit_tpm),
                )
        return tid

    def find_by_token(self, raw_token: str) -> tuple[str, UserIdentity] | None:
        if not raw_token or not raw_token.strip():
            return None

        thash = _hash_token(raw_token)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.user_id, t.is_active, t.revoked_at,
                           EXTRACT(EPOCH FROM t.expires_at) as expires_at_epoch,
                           u.username, u.role, u.is_active as user_active
                    FROM api_tokens t
                    JOIN users u ON t.user_id = u.id
                    WHERE t.token_hash = %s
                      AND t.is_active = TRUE
                      AND t.revoked_at IS NULL
                      AND t.expires_at > CURRENT_TIMESTAMP;
                    """,
                    (thash,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                role = UserRole(row["role"]) if row["role"] in UserRole.__members__.values() else UserRole.USER
                identity = UserIdentity(
                    user_id=row["user_id"],
                    username=row["username"],
                    roles=frozenset({role}),
                    is_authenticated=row["user_active"],
                    expires_at=row["expires_at_epoch"],
                )
                return row["id"], identity

    def revoke_token(self, raw_token: str) -> bool:
        if not raw_token or not raw_token.strip():
            return False

        thash = _hash_token(raw_token)
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE api_tokens
                    SET is_active = FALSE, revoked_at = CURRENT_TIMESTAMP
                    WHERE token_hash = %s;
                    """,
                    (thash,),
                )
                return cur.rowcount > 0

    def revoke_token_by_id(self, token_id: str, user_id: str | None = None) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        """
                        UPDATE api_tokens
                        SET is_active = FALSE, revoked_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND user_id = %s;
                        """,
                        (token_id, user_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE api_tokens
                        SET is_active = FALSE, revoked_at = CURRENT_TIMESTAMP
                        WHERE id = %s;
                        """,
                        (token_id,),
                    )
                return cur.rowcount > 0

    def list_tokens_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, prefix,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM expires_at) as expires_at,
                           EXTRACT(EPOCH FROM revoked_at) as revoked_at,
                           is_active, rate_limit_rpm, rate_limit_tpm
                    FROM api_tokens
                    WHERE user_id = %s
                    ORDER BY created_at DESC;
                    """,
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]


class PostgresConversationRepository(BaseConversationRepository):
    """PostgreSQL conversation repository enforcing composite ownership integrity."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def create_conversation(
        self,
        user_id: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> str:
        cid = conversation_id or f"conv_{uuid4().hex[:12]}"
        meta_json = json.dumps(metadata or {})
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Ensure user exists
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active)
                    VALUES (%s, %s, 'user', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (user_id, user_id),
                )
                cur.execute(
                    """
                    INSERT INTO conversations (id, user_id, title, metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    """,
                    (cid, user_id, title or "New Conversation", meta_json),
                )
        return cid

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, title, metadata,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM updated_at) as updated_at
                    FROM conversations
                    WHERE id = %s AND user_id = %s;
                    """,
                    (conversation_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                res = dict(row)
                if isinstance(res.get("metadata"), str):
                    res["metadata"] = json.loads(res["metadata"])
                return res

    def list_conversations(self, user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, title, metadata,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM updated_at) as updated_at
                    FROM conversations
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s;
                    """,
                    (user_id, limit, offset),
                )
                rows = []
                for r in cur.fetchall():
                    item = dict(r)
                    if isinstance(item.get("metadata"), str):
                        item["metadata"] = json.loads(item["metadata"])
                    rows.append(item)
                return rows

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE id = %s AND user_id = %s;",
                    (conversation_id, user_id),
                )
                return cur.rowcount > 0

    def add_turn(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        turn_index: int | None = None,
        model: str = "",
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> str:
        tid = turn_id or f"turn_{uuid4().hex[:12]}"
        meta_json = json.dumps(metadata or {})
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Verify conversation exists with composite ownership check
                cur.execute(
                    "SELECT 1 FROM conversations WHERE id = %s AND user_id = %s;",
                    (conversation_id, user_id),
                )
                if not cur.fetchone():
                    raise ValueError(
                        f"Composite foreign key violation: conversation '{conversation_id}' does not exist for user '{user_id}'."
                    )

                if turn_index is None:
                    cur.execute(
                        "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_idx FROM conversation_turns WHERE conversation_id = %s;",
                        (conversation_id,),
                    )
                    idx_row = cur.fetchone()
                    t_idx = idx_row["next_idx"] if idx_row else 0
                else:
                    t_idx = turn_index

                cur.execute(
                    """
                    INSERT INTO conversation_turns (
                        id, conversation_id, user_id, turn_index, role, content,
                        timestamp, model, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s
                    );
                    """,
                    (tid, conversation_id, user_id, t_idx, role, content, model, meta_json),
                )
                cur.execute(
                    "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s;",
                    (conversation_id, user_id),
                )
        return tid

    def get_turns(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, conversation_id, user_id, turn_index, role, content,
                           EXTRACT(EPOCH FROM timestamp) as timestamp, model, metadata
                    FROM conversation_turns
                    WHERE conversation_id = %s AND user_id = %s
                    ORDER BY turn_index ASC;
                    """,
                    (conversation_id, user_id),
                )
                rows = []
                for r in cur.fetchall():
                    item = dict(r)
                    if isinstance(item.get("metadata"), str):
                        item["metadata"] = json.loads(item["metadata"])
                    rows.append(item)
                return rows

    def clear_turns(self, conversation_id: str, user_id: str) -> int:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversation_turns WHERE conversation_id = %s AND user_id = %s;",
                    (conversation_id, user_id),
                )
                return cur.rowcount


class PostgresMemoryRepository(BaseMemoryRepository):
    """PostgreSQL unified memory repository."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def record_memory(
        self,
        user_id: str,
        category: str,
        content: str,
        confidence: float = 1.0,
        importance: float = 0.5,
        tags: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> UnifiedMemoryRecord:
        rec_id = memory_id or f"mem_{uuid4().hex[:12]}"
        cat_enum = MemoryCategory(category) if isinstance(category, str) else category
        tags_json = json.dumps(tags or [])
        prov_json = json.dumps(provenance or {})
        meta_json = json.dumps(metadata or {})
        now = time.time()

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Ensure user exists
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active)
                    VALUES (%s, %s, 'user', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (user_id, user_id),
                )
                cur.execute(
                    """
                    INSERT INTO user_memories (
                        id, user_id, category, content, confidence, importance,
                        tags, provenance, metadata, created_at, last_accessed
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET content = EXCLUDED.content,
                        confidence = EXCLUDED.confidence,
                        importance = EXCLUDED.importance,
                        tags = EXCLUDED.tags,
                        metadata = EXCLUDED.metadata,
                        last_accessed = CURRENT_TIMESTAMP;
                    """,
                    (rec_id, user_id, cat_enum.value, content, confidence, importance, tags_json, prov_json, meta_json),
                )

        return UnifiedMemoryRecord(
            record_id=rec_id,
            category=cat_enum,
            content=content,
            user_id=user_id,
            confidence=confidence,
            importance=importance,
            created_at=now,
            last_accessed=now,
            tags=list(tags or []),
            provenance=dict(provenance or {}),
            metadata=dict(metadata or {}),
        )

    def query_memories(
        self,
        user_id: str,
        category: str | None = None,
        tag: str | None = None,
        query: str = "",
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[UnifiedMemoryRecord]:
        query_sql = """
            SELECT id, user_id, category, content, confidence, importance,
                   tags, provenance, metadata,
                   EXTRACT(EPOCH FROM created_at) as created_at,
                   EXTRACT(EPOCH FROM last_accessed) as last_accessed
            FROM user_memories
            WHERE user_id = %s
              AND confidence >= %s
        """
        params: list[Any] = [user_id, min_confidence]

        if category:
            cat_val = category.value if hasattr(category, "value") else str(category)
            query_sql += " AND category = %s"
            params.append(cat_val)

        if query and query.strip():
            query_sql += " AND content ILIKE %s"
            params.append(f"%{query.strip()}%")

        query_sql += " ORDER BY importance DESC, last_accessed DESC LIMIT %s;"
        params.append(limit)

        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query_sql, tuple(params))
                results: list[UnifiedMemoryRecord] = []
                for r in cur.fetchall():
                    raw_tags = r["tags"]
                    if isinstance(raw_tags, str):
                        raw_tags = json.loads(raw_tags)
                    if tag is not None and tag not in (raw_tags or []):
                        continue

                    raw_prov = r["provenance"]
                    if isinstance(raw_prov, str):
                        raw_prov = json.loads(raw_prov)

                    raw_meta = r["metadata"]
                    if isinstance(raw_meta, str):
                        raw_meta = json.loads(raw_meta)

                    results.append(
                        UnifiedMemoryRecord(
                            record_id=r["id"],
                            category=MemoryCategory(r["category"]),
                            content=r["content"],
                            user_id=r["user_id"],
                            confidence=r["confidence"],
                            importance=r["importance"],
                            created_at=r["created_at"] or time.time(),
                            last_accessed=r["last_accessed"] or time.time(),
                            tags=list(raw_tags or []),
                            provenance=dict(raw_prov or {}),
                            metadata=dict(raw_meta or {}),
                        )
                    )
                return results

    def get_memory(self, memory_id: str, user_id: str) -> UnifiedMemoryRecord | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, category, content, confidence, importance,
                           tags, provenance, metadata,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM last_accessed) as last_accessed
                    FROM user_memories
                    WHERE id = %s AND user_id = %s;
                    """,
                    (memory_id, user_id),
                )
                r = cur.fetchone()
                if not r:
                    return None

                raw_tags = r["tags"]
                if isinstance(raw_tags, str):
                    raw_tags = json.loads(raw_tags)
                raw_prov = r["provenance"]
                if isinstance(raw_prov, str):
                    raw_prov = json.loads(raw_prov)
                raw_meta = r["metadata"]
                if isinstance(raw_meta, str):
                    raw_meta = json.loads(raw_meta)

                return UnifiedMemoryRecord(
                    record_id=r["id"],
                    category=MemoryCategory(r["category"]),
                    content=r["content"],
                    user_id=r["user_id"],
                    confidence=r["confidence"],
                    importance=r["importance"],
                    created_at=r["created_at"] or time.time(),
                    last_accessed=r["last_accessed"] or time.time(),
                    tags=list(raw_tags or []),
                    provenance=dict(raw_prov or {}),
                    metadata=dict(raw_meta or {}),
                )

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_memories WHERE id = %s AND user_id = %s;",
                    (memory_id, user_id),
                )
                return cur.rowcount > 0

    def clear_memories(self, user_id: str) -> int:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_memories WHERE user_id = %s;", (user_id,))
                return cur.rowcount


class PostgresExperienceRepository(BaseExperienceRepository):
    """PostgreSQL episodic experience repository."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def record_experience(
        self,
        user_id: str,
        task_description: str,
        plan_summary: str,
        action_sequence: list[str] | None = None,
        outcome: str = "success",
        reward_score: float = 1.0,
        lessons_learned: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        experience_id: str | None = None,
    ) -> EpisodicExperienceRecord:
        exp_id = experience_id or f"exp_{uuid4().hex[:12]}"
        now = time.time()
        actions_json = json.dumps(action_sequence or [])
        lessons_json = json.dumps(lessons_learned or [])
        prov_json = json.dumps(provenance or {})
        meta_json = json.dumps(metadata or {})

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Ensure user exists
                cur.execute(
                    """
                    INSERT INTO users (id, username, role, is_active)
                    VALUES (%s, %s, 'user', TRUE)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (user_id, user_id),
                )
                cur.execute(
                    """
                    INSERT INTO user_experiences (
                        id, user_id, task_description, plan_summary, action_sequence,
                        outcome, reward_score, lessons_learned, provenance, metadata, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                    );
                    """,
                    (
                        exp_id,
                        user_id,
                        task_description,
                        plan_summary,
                        actions_json,
                        outcome,
                        reward_score,
                        lessons_json,
                        prov_json,
                        meta_json,
                    ),
                )

        return EpisodicExperienceRecord(
            experience_id=exp_id,
            task_description=task_description,
            plan_summary=plan_summary,
            user_id=user_id,
            action_sequence=list(action_sequence or []),
            outcome=outcome,
            reward_score=reward_score,
            lessons_learned=list(lessons_learned or []),
            created_at=now,
            provenance=dict(provenance or {}),
            metadata=dict(metadata or {}),
        )

    def query_experiences(
        self,
        user_id: str,
        outcome: str | None = None,
        query: str = "",
        limit: int = 20,
    ) -> list[EpisodicExperienceRecord]:
        query_sql = """
            SELECT id, user_id, task_description, plan_summary, action_sequence,
                   outcome, reward_score, lessons_learned, provenance, metadata,
                   EXTRACT(EPOCH FROM created_at) as created_at
            FROM user_experiences
            WHERE user_id = %s
        """
        params: list[Any] = [user_id]

        if outcome:
            query_sql += " AND outcome = %s"
            params.append(outcome)

        if query and query.strip():
            query_sql += " AND (task_description ILIKE %s OR plan_summary ILIKE %s)"
            q_pat = f"%{query.strip()}%"
            params.extend([q_pat, q_pat])

        query_sql += " ORDER BY reward_score DESC, created_at DESC LIMIT %s;"
        params.append(limit)

        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query_sql, tuple(params))
                results: list[EpisodicExperienceRecord] = []
                for r in cur.fetchall():
                    raw_actions = r["action_sequence"]
                    if isinstance(raw_actions, str):
                        raw_actions = json.loads(raw_actions)

                    raw_lessons = r["lessons_learned"]
                    if isinstance(raw_lessons, str):
                        raw_lessons = json.loads(raw_lessons)

                    raw_prov = r["provenance"]
                    if isinstance(raw_prov, str):
                        raw_prov = json.loads(raw_prov)

                    raw_meta = r["metadata"]
                    if isinstance(raw_meta, str):
                        raw_meta = json.loads(raw_meta)

                    results.append(
                        EpisodicExperienceRecord(
                            experience_id=r["id"],
                            task_description=r["task_description"],
                            plan_summary=r["plan_summary"],
                            user_id=r["user_id"],
                            action_sequence=list(raw_actions or []),
                            outcome=r["outcome"],
                            reward_score=r["reward_score"],
                            lessons_learned=list(raw_lessons or []),
                            created_at=r["created_at"] or time.time(),
                            provenance=dict(raw_prov or {}),
                            metadata=dict(raw_meta or {}),
                        )
                    )
                return results

    def get_experience(self, experience_id: str, user_id: str) -> EpisodicExperienceRecord | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, task_description, plan_summary, action_sequence,
                           outcome, reward_score, lessons_learned, provenance, metadata,
                           EXTRACT(EPOCH FROM created_at) as created_at
                    FROM user_experiences
                    WHERE id = %s AND user_id = %s;
                    """,
                    (experience_id, user_id),
                )
                r = cur.fetchone()
                if not r:
                    return None

                raw_actions = r["action_sequence"]
                if isinstance(raw_actions, str):
                    raw_actions = json.loads(raw_actions)

                raw_lessons = r["lessons_learned"]
                if isinstance(raw_lessons, str):
                    raw_lessons = json.loads(raw_lessons)

                raw_prov = r["provenance"]
                if isinstance(raw_prov, str):
                    raw_prov = json.loads(raw_prov)

                raw_meta = r["metadata"]
                if isinstance(raw_meta, str):
                    raw_meta = json.loads(raw_meta)

                return EpisodicExperienceRecord(
                    experience_id=r["id"],
                    task_description=r["task_description"],
                    plan_summary=r["plan_summary"],
                    user_id=r["user_id"],
                    action_sequence=list(raw_actions or []),
                    outcome=r["outcome"],
                    reward_score=r["reward_score"],
                    lessons_learned=list(raw_lessons or []),
                    created_at=r["created_at"] or time.time(),
                    provenance=dict(raw_prov or {}),
                    metadata=dict(raw_meta or {}),
                )

    def delete_experience(self, experience_id: str, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_experiences WHERE id = %s AND user_id = %s;",
                    (experience_id, user_id),
                )
                return cur.rowcount > 0


class PostgresCheckpointRepository(BaseCheckpointRepository):
    """PostgreSQL runtime checkpoint repository."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def save_checkpoint(
        self,
        checkpoint_type: str,
        state_payload: dict[str, Any],
        user_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> str:
        cid = checkpoint_id or f"chk_{uuid4().hex[:12]}"
        payload_json = json.dumps(state_payload, sort_keys=True)
        checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        """
                        INSERT INTO users (id, username, role, is_active)
                        VALUES (%s, %s, 'user', TRUE)
                        ON CONFLICT (id) DO NOTHING;
                        """,
                        (user_id, user_id),
                    )
                cur.execute(
                    """
                    INSERT INTO runtime_checkpoints (
                        id, user_id, checkpoint_type, state_payload, checksum, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                    );
                    """,
                    (cid, user_id, checkpoint_type, payload_json, checksum),
                )
        return cid

    def get_latest_checkpoint(
        self,
        checkpoint_type: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id is None:
                    cur.execute(
                        """
                        SELECT id, user_id, checkpoint_type, state_payload, checksum,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM runtime_checkpoints
                        WHERE checkpoint_type = %s AND user_id IS NULL
                        ORDER BY created_at DESC LIMIT 1;
                        """,
                        (checkpoint_type,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, user_id, checkpoint_type, state_payload, checksum,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM runtime_checkpoints
                        WHERE checkpoint_type = %s AND user_id = %s
                        ORDER BY created_at DESC LIMIT 1;
                        """,
                        (checkpoint_type, user_id),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                res = dict(row)
                if isinstance(res.get("state_payload"), str):
                    res["state_payload"] = json.loads(res["state_payload"])
                return res

    def get_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id is None:
                    cur.execute(
                        """
                        SELECT id, user_id, checkpoint_type, state_payload, checksum,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM runtime_checkpoints
                        WHERE id = %s;
                        """,
                        (checkpoint_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, user_id, checkpoint_type, state_payload, checksum,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM runtime_checkpoints
                        WHERE id = %s AND (user_id = %s OR user_id IS NULL);
                        """,
                        (checkpoint_id, user_id),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                res = dict(row)
                if isinstance(res.get("state_payload"), str):
                    res["state_payload"] = json.loads(res["state_payload"])
                return res

    def list_checkpoints(
        self,
        checkpoint_type: str | None = None,
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query_sql = """
            SELECT id, user_id, checkpoint_type, state_payload, checksum,
                   EXTRACT(EPOCH FROM created_at) as created_at
            FROM runtime_checkpoints
            WHERE 1=1
        """
        params: list[Any] = []
        if checkpoint_type:
            query_sql += " AND checkpoint_type = %s"
            params.append(checkpoint_type)
        if user_id is not None:
            query_sql += " AND user_id = %s"
            params.append(user_id)

        query_sql += " ORDER BY created_at DESC LIMIT %s;"
        params.append(limit)

        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query_sql, tuple(params))
                rows = []
                for r in cur.fetchall():
                    item = dict(r)
                    if isinstance(item.get("state_payload"), str):
                        item["state_payload"] = json.loads(item["state_payload"])
                    rows.append(item)
                return rows

    def delete_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                if user_id is None:
                    cur.execute("DELETE FROM runtime_checkpoints WHERE id = %s;", (checkpoint_id,))
                else:
                    cur.execute(
                        "DELETE FROM runtime_checkpoints WHERE id = %s AND user_id = %s;",
                        (checkpoint_id, user_id),
                    )
                return cur.rowcount > 0
