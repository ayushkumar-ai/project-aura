"""M42 — In-Memory Repository Implementations for Project AURA.

Provides fast, thread-safe in-memory repository implementations of all M42 contracts
for unit testing, development environments, and offline validation.
Enforces the same semantic isolation, composite ownership constraints, and token hashing
as the PostgreSQL production implementation.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from typing import Any
from uuid import uuid4

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
    BaseKnowledgeRepository,
    BaseMemoryRepository,
    BaseUserPreferencesRepository,
    BaseUserRepository,
    BaseVectorSearchRepository,
    BaseTaskRepository,
    BaseApprovalRepository,
)


def _hash_token(raw_token: str) -> str:
    """Compute SHA256 hexadecimal digest for token secret."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


class InMemoryUserRepository(BaseUserRepository):
    """In-memory user repository."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users_by_id: dict[str, UserIdentity] = {}
        self._users_by_username: dict[str, str] = {}

    def get_by_id(self, user_id: str) -> UserIdentity | None:
        with self._lock:
            return self._users_by_id.get(user_id)

    def get_by_username(self, username: str) -> UserIdentity | None:
        with self._lock:
            uid = self._users_by_username.get(username.lower())
            if uid:
                return self._users_by_id.get(uid)
            return None

    def save(self, user: UserIdentity) -> UserIdentity:
        with self._lock:
            # Check for unique username conflict
            existing_uid = self._users_by_username.get(user.username.lower())
            if existing_uid and existing_uid != user.user_id:
                raise ValueError(f"Username '{user.username}' is already taken.")

            # Remove old username mapping if changed
            old_user = self._users_by_id.get(user.user_id)
            if old_user and old_user.username.lower() != user.username.lower():
                self._users_by_username.pop(old_user.username.lower(), None)

            self._users_by_id[user.user_id] = user
            self._users_by_username[user.username.lower()] = user.user_id
            return user

    def delete(self, user_id: str) -> bool:
        with self._lock:
            user = self._users_by_id.pop(user_id, None)
            if user:
                self._users_by_username.pop(user.username.lower(), None)
                return True
            return False

    def list_users(self, limit: int = 100, offset: int = 0) -> list[UserIdentity]:
        with self._lock:
            all_users = list(self._users_by_id.values())
            return all_users[offset : offset + limit]


class InMemoryUserPreferencesRepository(BaseUserPreferencesRepository):
    """In-memory user preferences repository."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._prefs: dict[str, UserPreferences] = {}

    def get(self, user_id: str) -> UserPreferences:
        with self._lock:
            uid = user_id if (user_id and str(user_id).strip()) else "default"
            if uid in self._prefs:
                return UserPreferences.from_dict(self._prefs[uid].to_dict())
            return UserPreferences(user_id=uid)

    def save(self, user_id: str, preferences: UserPreferences | dict[str, Any]) -> UserPreferences:
        with self._lock:
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

            self._prefs[uid] = updated
            return UserPreferences.from_dict(updated.to_dict())

    def delete(self, user_id: str) -> bool:
        with self._lock:
            return self._prefs.pop(user_id, None) is not None


class InMemoryApiTokenRepository(BaseApiTokenRepository):
    """In-memory token repository with SHA256 hashing and constant-time matching."""

    def __init__(self, user_repo: BaseUserRepository) -> None:
        self._lock = threading.RLock()
        self._user_repo = user_repo
        # token_hash -> token_record dict
        self._tokens_by_hash: dict[str, dict[str, Any]] = {}
        # token_id -> token_hash
        self._id_to_hash: dict[str, str] = {}

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

        with self._lock:
            tid = token_id or f"tok_{uuid4().hex[:12]}"
            thash = _hash_token(raw_token)
            prefix = raw_token.strip()[:8] if len(raw_token.strip()) >= 8 else raw_token.strip()
            now = time.time()
            expires_at = now + expires_in_seconds

            record = {
                "id": tid,
                "user_id": user_id,
                "token_hash": thash,
                "prefix": prefix,
                "created_at": now,
                "expires_at": expires_at,
                "revoked_at": None,
                "is_active": True,
                "rate_limit_rpm": rate_limit_rpm,
                "rate_limit_tpm": rate_limit_tpm,
            }
            self._tokens_by_hash[thash] = record
            self._id_to_hash[tid] = thash
            return tid

    def find_by_token(self, raw_token: str) -> tuple[str, UserIdentity] | None:
        if not raw_token or not raw_token.strip():
            return None

        with self._lock:
            thash = _hash_token(raw_token)
            matched_record: dict[str, Any] | None = None

            # Constant-time comparison across stored token hashes
            for registered_hash, rec in self._tokens_by_hash.items():
                if hmac.compare_digest(registered_hash, thash):
                    matched_record = rec
                    break

            if matched_record is None:
                return None

            if not matched_record.get("is_active", True) or matched_record.get("revoked_at") is not None:
                return None

            now = time.time()
            if now > matched_record.get("expires_at", 0):
                return None

            user = self._user_repo.get_by_id(matched_record["user_id"])
            if user is None:
                return None

            return matched_record["id"], user

    def revoke_token(self, raw_token: str) -> bool:
        if not raw_token or not raw_token.strip():
            return False

        with self._lock:
            thash = _hash_token(raw_token)
            for registered_hash, rec in self._tokens_by_hash.items():
                if hmac.compare_digest(registered_hash, thash):
                    rec["is_active"] = False
                    rec["revoked_at"] = time.time()
                    return True
            return False

    def revoke_token_by_id(self, token_id: str, user_id: str | None = None) -> bool:
        with self._lock:
            thash = self._id_to_hash.get(token_id)
            if not thash or thash not in self._tokens_by_hash:
                return False
            rec = self._tokens_by_hash[thash]
            if user_id and rec.get("user_id") != user_id:
                return False
            rec["is_active"] = False
            rec["revoked_at"] = time.time()
            return True

    def list_tokens_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            results = []
            for rec in self._tokens_by_hash.values():
                if rec.get("user_id") == user_id:
                    # Scrub hash from user-facing result
                    clean = {
                        "id": rec["id"],
                        "user_id": rec["user_id"],
                        "prefix": rec["prefix"],
                        "created_at": rec["created_at"],
                        "expires_at": rec["expires_at"],
                        "revoked_at": rec["revoked_at"],
                        "is_active": rec["is_active"],
                        "rate_limit_rpm": rec["rate_limit_rpm"],
                        "rate_limit_tpm": rec["rate_limit_tpm"],
                    }
                    results.append(clean)
            return sorted(results, key=lambda x: x["created_at"], reverse=True)


class InMemoryConversationRepository(BaseConversationRepository):
    """In-memory conversation repository enforcing composite ownership integrity."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (conv_id, user_id) -> conversation dict
        self._conversations: dict[tuple[str, str], dict[str, Any]] = {}
        # (conv_id, user_id) -> list of turns
        self._turns: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def create_conversation(
        self,
        user_id: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> str:
        with self._lock:
            cid = conversation_id or f"conv_{uuid4().hex[:12]}"
            key = (cid, user_id)
            now = time.time()
            conv = {
                "id": cid,
                "user_id": user_id,
                "title": title or "New Conversation",
                "created_at": now,
                "updated_at": now,
                "metadata": dict(metadata or {}),
            }
            self._conversations[key] = conv
            self._turns[key] = []
            return cid

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            key = (conversation_id, user_id)
            conv = self._conversations.get(key)
            return dict(conv) if conv else None

    def list_conversations(self, user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            user_convs = [
                dict(c) for (cid, uid), c in self._conversations.items() if uid == user_id
            ]
            user_convs.sort(key=lambda x: x["updated_at"], reverse=True)
            return user_convs[offset : offset + limit]

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        with self._lock:
            key = (conversation_id, user_id)
            if key in self._conversations:
                self._conversations.pop(key, None)
                self._turns.pop(key, None)
                return True
            return False

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
        with self._lock:
            key = (conversation_id, user_id)
            if key not in self._conversations:
                raise ValueError(
                    f"Composite foreign key violation: conversation '{conversation_id}' does not exist for user '{user_id}'."
                )

            tid = turn_id or f"turn_{uuid4().hex[:12]}"
            turn_list = self._turns[key]
            t_idx = turn_index if turn_index is not None else len(turn_list)
            now = time.time()

            turn = {
                "id": tid,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "turn_index": t_idx,
                "role": role,
                "content": content,
                "timestamp": now,
                "model": model,
                "metadata": dict(metadata or {}),
            }
            turn_list.append(turn)
            self._conversations[key]["updated_at"] = now
            return tid

    def get_turns(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            key = (conversation_id, user_id)
            if key not in self._conversations:
                return []
            return [dict(t) for t in self._turns.get(key, [])]

    def clear_turns(self, conversation_id: str, user_id: str) -> int:
        with self._lock:
            key = (conversation_id, user_id)
            if key in self._turns:
                count = len(self._turns[key])
                self._turns[key].clear()
                return count
            return 0


class InMemoryMemoryRepository(BaseMemoryRepository):
    """In-memory repository for unified memories with user isolation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._memories: dict[str, UnifiedMemoryRecord] = {}

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
        with self._lock:
            rec_id = memory_id or f"mem_{uuid4().hex[:12]}"
            cat_enum = MemoryCategory(category) if isinstance(category, str) else category
            now = time.time()
            record = UnifiedMemoryRecord(
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
            self._memories[rec_id] = record
            return record

    def query_memories(
        self,
        user_id: str,
        category: str | None = None,
        tag: str | None = None,
        query: str = "",
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[UnifiedMemoryRecord]:
        with self._lock:
            results: list[UnifiedMemoryRecord] = []
            q_lower = query.lower().strip() if query else ""
            cat_enum = MemoryCategory(category) if category else None

            for mem in self._memories.values():
                if mem.user_id != user_id:
                    continue
                if cat_enum is not None and mem.category != cat_enum:
                    continue
                if mem.confidence < min_confidence:
                    continue
                if tag is not None and tag not in mem.tags:
                    continue
                if q_lower and q_lower not in mem.content.lower():
                    continue
                results.append(mem)

            results.sort(key=lambda m: (m.importance, m.last_accessed), reverse=True)
            return results[:limit]

    def get_memory(self, memory_id: str, user_id: str) -> UnifiedMemoryRecord | None:
        with self._lock:
            mem = self._memories.get(memory_id)
            if mem and mem.user_id == user_id:
                return mem
            return None

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        with self._lock:
            mem = self._memories.get(memory_id)
            if mem and mem.user_id == user_id:
                del self._memories[memory_id]
                return True
            return False

    def clear_memories(self, user_id: str) -> int:
        with self._lock:
            to_delete = [mid for mid, m in self._memories.items() if m.user_id == user_id]
            for mid in to_delete:
                del self._memories[mid]
            return len(to_delete)


class InMemoryExperienceRepository(BaseExperienceRepository):
    """In-memory repository for episodic experiences with user isolation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._experiences: dict[str, EpisodicExperienceRecord] = {}

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
        with self._lock:
            exp_id = experience_id or f"exp_{uuid4().hex[:12]}"
            now = time.time()
            record = EpisodicExperienceRecord(
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
            self._experiences[exp_id] = record
            return record

    def query_experiences(
        self,
        user_id: str,
        outcome: str | None = None,
        query: str = "",
        limit: int = 20,
    ) -> list[EpisodicExperienceRecord]:
        with self._lock:
            results: list[EpisodicExperienceRecord] = []
            q_lower = query.lower().strip() if query else ""

            for exp in self._experiences.values():
                if exp.user_id != user_id:
                    continue
                if outcome is not None and exp.outcome != outcome:
                    continue
                if q_lower and (q_lower not in exp.task_description.lower() and q_lower not in exp.plan_summary.lower()):
                    continue
                results.append(exp)

            results.sort(key=lambda e: (e.reward_score, e.created_at), reverse=True)
            return results[:limit]

    def get_experience(self, experience_id: str, user_id: str) -> EpisodicExperienceRecord | None:
        with self._lock:
            exp = self._experiences.get(experience_id)
            if exp and exp.user_id == user_id:
                return exp
            return None

    def delete_experience(self, experience_id: str, user_id: str) -> bool:
        with self._lock:
            exp = self._experiences.get(experience_id)
            if exp and exp.user_id == user_id:
                del self._experiences[experience_id]
                return True
            return False


class InMemoryCheckpointRepository(BaseCheckpointRepository):
    """In-memory repository for runtime checkpoints."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._checkpoints: dict[str, dict[str, Any]] = {}

    def save_checkpoint(
        self,
        checkpoint_type: str,
        state_payload: dict[str, Any],
        user_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> str:
        with self._lock:
            cid = checkpoint_id or f"chk_{uuid4().hex[:12]}"
            import json
            serialized = json.dumps(state_payload, sort_keys=True)
            checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            now = time.time()
            record = {
                "id": cid,
                "user_id": user_id,
                "checkpoint_type": checkpoint_type,
                "state_payload": state_payload,
                "checksum": checksum,
                "created_at": now,
            }
            self._checkpoints[cid] = record
            return cid

    def get_latest_checkpoint(
        self,
        checkpoint_type: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            matches = [
                c for c in self._checkpoints.values()
                if c["checkpoint_type"] == checkpoint_type and c["user_id"] == user_id
            ]
            if not matches:
                return None
            matches.sort(key=lambda x: x["created_at"], reverse=True)
            return dict(matches[0])

    def get_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            chk = self._checkpoints.get(checkpoint_id)
            if not chk:
                return None
            if user_id is not None and chk.get("user_id") != user_id:
                return None
            return dict(chk)

    def list_checkpoints(
        self,
        checkpoint_type: str | None = None,
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._lock:
            results = []
            for chk in self._checkpoints.values():
                if checkpoint_type is not None and chk["checkpoint_type"] != checkpoint_type:
                    continue
                if user_id is not None and chk["user_id"] != user_id:
                    continue
                results.append(dict(chk))
            results.sort(key=lambda x: x["created_at"], reverse=True)
            return results[:limit]

    def delete_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> bool:
        with self._lock:
            chk = self._checkpoints.get(checkpoint_id)
            if not chk:
                return False
            if user_id is not None and chk.get("user_id") != user_id:
                return False
            del self._checkpoints[checkpoint_id]
            return True


def _cosine_similarity(vec_a: list[float] | None, vec_b: list[float] | None) -> float:
    """Compute cosine similarity between two float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 1e-9 or norm_b <= 1e-9:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class InMemoryKnowledgeRepository(BaseKnowledgeRepository):
    """In-memory knowledge and chunk repository with user isolation and visibility control."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._documents: dict[str, dict[str, Any]] = {}
        self._chunks: dict[str, dict[str, Any]] = {}

    def save_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        doc_checksum: str,
        user_id: str | None = None,
        visibility: str = "public",
        authority: str = "verified",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            existing = self._documents.get(doc_id)
            created_at = existing["created_at"] if existing else now
            doc = {
                "id": doc_id,
                "user_id": user_id,
                "title": title,
                "content": content,
                "doc_checksum": doc_checksum,
                "visibility": visibility.lower(),
                "authority": authority,
                "tags": list(tags or []),
                "metadata": dict(metadata or {}),
                "created_at": created_at,
                "updated_at": now,
            }
            self._documents[doc_id] = doc
            return dict(doc)

    def get_document(self, doc_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            doc = self._documents.get(doc_id)
            if not doc:
                return None
            vis = doc.get("visibility", "public")
            doc_uid = doc.get("user_id")
            if vis == "public":
                return dict(doc)
            if user_id is not None and doc_uid == user_id:
                return dict(doc)
            return None

    def delete_document(self, doc_id: str, user_id: str | None = None) -> bool:
        with self._lock:
            doc = self._documents.get(doc_id)
            if not doc:
                return False
            if user_id is not None and doc.get("user_id") is not None and doc.get("user_id") != user_id:
                return False
            del self._documents[doc_id]
            # Cascade delete chunks
            to_delete = [cid for cid, chk in self._chunks.items() if chk.get("doc_id") == doc_id]
            for cid in to_delete:
                del self._chunks[cid]
            return True

    def list_documents(
        self,
        user_id: str | None = None,
        visibility: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            results = []
            for doc in self._documents.values():
                vis = doc.get("visibility", "public")
                doc_uid = doc.get("user_id")
                if visibility and vis != visibility:
                    continue
                if vis == "public" or (user_id is not None and doc_uid == user_id):
                    results.append(dict(doc))
            results.sort(key=lambda x: x["created_at"], reverse=True)
            return results[offset : offset + limit]

    def save_chunks(self, chunks: list[dict[str, Any]], user_id: str | None = None) -> int:
        with self._lock:
            saved = 0
            for c in chunks:
                cid = c["id"]
                self._chunks[cid] = {
                    "id": cid,
                    "doc_id": c["doc_id"],
                    "user_id": c.get("user_id") or user_id,
                    "chunk_index": c["chunk_index"],
                    "content": c["content"],
                    "char_start": c.get("char_start", 0),
                    "char_end": c.get("char_end", 0),
                    "chunk_hash": c.get("chunk_hash", ""),
                    "embedding": list(c["embedding"]) if c.get("embedding") is not None else None,
                    "metadata": dict(c.get("metadata", {})),
                    "created_at": time.time(),
                }
                saved += 1
            return saved

    def get_chunks_for_doc(self, doc_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            doc = self.get_document(doc_id, user_id=user_id)
            if not doc:
                return []
            results = [dict(c) for c in self._chunks.values() if c.get("doc_id") == doc_id]
            results.sort(key=lambda x: x["chunk_index"])
            return results

    def delete_chunks_for_doc(self, doc_id: str, user_id: str | None = None) -> int:
        with self._lock:
            to_delete = [cid for cid, chk in self._chunks.items() if chk.get("doc_id") == doc_id]
            for cid in to_delete:
                del self._chunks[cid]
            return len(to_delete)


class InMemoryVectorSearchRepository(BaseVectorSearchRepository):
    """In-memory vector similarity search repository with strict multi-tenant isolation."""

    def __init__(
        self,
        knowledge_repo: InMemoryKnowledgeRepository | None = None,
        memory_repo: InMemoryMemoryRepository | None = None,
        experience_repo: InMemoryExperienceRepository | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._knowledge_repo = knowledge_repo or InMemoryKnowledgeRepository()
        self._memory_repo = memory_repo or InMemoryMemoryRepository()
        self._experience_repo = experience_repo or InMemoryExperienceRepository()
        # memory_id -> (user_id, embedding)
        self._memory_embeddings: dict[str, tuple[str, list[float]]] = {}
        # experience_id -> (user_id, embedding)
        self._experience_embeddings: dict[str, tuple[str, list[float]]] = {}

    def search_knowledge_chunks(
        self,
        query_vector: list[float],
        user_id: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            results: list[dict[str, Any]] = []
            for chk in self._knowledge_repo._chunks.values():
                doc_id = chk.get("doc_id")
                doc = self._knowledge_repo._documents.get(doc_id)
                if not doc:
                    continue

                vis = doc.get("visibility", "public")
                chunk_uid = chk.get("user_id") or doc.get("user_id")

                # Multi-tenant isolation filter:
                # Chunk matches if user_id matches OR (doc is public and user_id is None / any)
                is_accessible = False
                if vis == "public" and (chunk_uid is None or user_id is None or chunk_uid == user_id):
                    is_accessible = True
                elif user_id is not None and chunk_uid == user_id:
                    is_accessible = True

                if not is_accessible:
                    continue

                embedding = chk.get("embedding")
                if not embedding:
                    continue

                sim = _cosine_similarity(query_vector, embedding)
                if sim >= min_similarity:
                    item = dict(chk)
                    item["similarity"] = sim
                    item["score"] = sim
                    item["title"] = doc.get("title", "")
                    item["authority"] = doc.get("authority", "verified")
                    item["tags"] = doc.get("tags", [])
                    results.append(item)

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

    def search_memories(
        self,
        query_vector: list[float],
        user_id: str,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            results: list[dict[str, Any]] = []
            for mem_id, (m_uid, emb) in self._memory_embeddings.items():
                if m_uid != user_id:
                    continue
                mem = self._memory_repo.get_memory(mem_id, user_id=user_id)
                if not mem:
                    continue
                sim = _cosine_similarity(query_vector, emb)
                if sim >= min_similarity:
                    results.append({
                        "memory_id": mem.record_id,
                        "user_id": user_id,
                        "category": mem.category.value,
                        "content": mem.content,
                        "confidence": mem.confidence,
                        "importance": mem.importance,
                        "tags": mem.tags,
                        "similarity": sim,
                        "score": sim * mem.confidence,
                        "metadata": mem.metadata,
                    })

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

    def search_experiences(
        self,
        query_vector: list[float],
        user_id: str,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            results: list[dict[str, Any]] = []
            for exp_id, (e_uid, emb) in self._experience_embeddings.items():
                if e_uid != user_id:
                    continue
                exp = self._experience_repo.get_experience(exp_id, user_id=user_id)
                if not exp:
                    continue
                sim = _cosine_similarity(query_vector, emb)
                if sim >= min_similarity:
                    results.append({
                        "experience_id": exp.experience_id,
                        "user_id": user_id,
                        "task_description": exp.task_description,
                        "plan_summary": exp.plan_summary,
                        "outcome": exp.outcome,
                        "reward_score": exp.reward_score,
                        "lessons_learned": exp.lessons_learned,
                        "similarity": sim,
                        "score": sim * max(0.1, exp.reward_score),
                        "metadata": exp.metadata,
                    })

            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:limit]

    def update_memory_embedding(self, memory_id: str, user_id: str, embedding: list[float]) -> bool:
        with self._lock:
            mem = self._memory_repo.get_memory(memory_id, user_id=user_id)
            if not mem:
                return False
            self._memory_embeddings[memory_id] = (user_id, list(embedding))
            return True

    def update_experience_embedding(self, experience_id: str, user_id: str, embedding: list[float]) -> bool:
        with self._lock:
            exp = self._experience_repo.get_experience(experience_id, user_id=user_id)
            if not exp:
                return False
            self._experience_embeddings[experience_id] = (user_id, list(embedding))
            return True



class InMemoryTaskRepository(BaseTaskRepository):
    """In-memory thread-safe task and step lifecycle repository."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._steps: dict[str, list[dict[str, Any]]] = {}
        # (user_id, idempotency_key) -> task_id
        self._idempotency_map: dict[tuple[str, str], str] = {}

    def create_task(
        self,
        user_id: str,
        title: str,
        goal: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: int = 600,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if idempotency_key and idempotency_key.strip():
                ik = (user_id, idempotency_key.strip())
                if ik in self._idempotency_map:
                    tid = self._idempotency_map[ik]
                    return dict(self._tasks[tid])

            eff_task_id = (task_id or str(uuid4())).strip()
            now = time.time()
            record = {
                "id": eff_task_id,
                "user_id": user_id,
                "title": title.strip(),
                "goal": goal.strip(),
                "context": dict(context or {}),
                "status": "pending",
                "result": None,
                "error_message": None,
                "idempotency_key": idempotency_key.strip() if idempotency_key else None,
                "timeout_seconds": int(timeout_seconds),
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "updated_at": now,
            }
            self._tasks[eff_task_id] = record
            if idempotency_key and idempotency_key.strip():
                self._idempotency_map[(user_id, idempotency_key.strip())] = eff_task_id
            return dict(record)

    def get_task(self, task_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id.strip())
            if not task or task["user_id"] != user_id:
                return None
            return dict(task)

    def list_tasks(
        self,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            matched = [
                dict(t) for t in self._tasks.values()
                if t["user_id"] == user_id and (not status or t["status"] == status.strip().lower())
            ]
            matched.sort(key=lambda x: x["created_at"], reverse=True)
            return matched[offset:offset + limit]

    def update_task_status(
        self,
        task_id: str,
        user_id: str,
        status: str,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        status_norm = status.strip().lower()
        valid_transitions: dict[str, tuple[str, ...]] = {
            "running": ("pending", "running"),
            "awaiting_approval": ("running", "awaiting_approval"),
            "pending": ("awaiting_approval", "pending", "running"),
            "completed": ("pending", "running"),
            "failed": ("pending", "running", "awaiting_approval"),
            "timed_out": ("pending", "running", "awaiting_approval"),
            "cancelled": ("pending", "running", "awaiting_approval", "cancelled"),
        }
        allowed_sources = valid_transitions.get(status_norm)
        if not allowed_sources:
            return False

        with self._lock:
            task = self._tasks.get(task_id.strip())
            if not task or task["user_id"] != user_id:
                return False

            if task.get("status") not in allowed_sources:
                return False

            task["status"] = status_norm
            now = time.time()
            task["updated_at"] = now

            if status_norm == "running" and not task.get("started_at"):
                task["started_at"] = now
            elif status_norm in ("completed", "failed", "cancelled", "timed_out"):
                if not task.get("completed_at"):
                    task["completed_at"] = now

            if error_message is not None:
                task["error_message"] = error_message
            if result is not None:
                task["result"] = result

            return True

    def acquire_next_pending_task(
        self,
        worker_id: str = "default",
        lock_timeout_seconds: int = 600,
    ) -> dict[str, Any] | None:
        with self._lock:
            pending_tasks = [t for t in self._tasks.values() if t["status"] == "pending"]
            if not pending_tasks:
                return None
            pending_tasks.sort(key=lambda x: x["created_at"])
            target = pending_tasks[0]
            now = time.time()
            target["status"] = "running"
            target["started_at"] = target.get("started_at") or now
            target["updated_at"] = now
            return dict(target)

    def create_or_update_step(
        self,
        task_id: str,
        user_id: str,
        step_index: int,
        name: str,
        status: str,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: dict[str, Any] | None = None,
        error_message: str | None = None,
        step_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id.strip())
            if not task or task["user_id"] != user_id:
                raise ValueError(f"Task '{task_id}' not found for user '{user_id}'")

            steps = self._steps.setdefault(task_id.strip(), [])
            existing_step = next((s for s in steps if s["step_index"] == int(step_index)), None)
            now = time.time()
            status_norm = status.strip().lower()

            if existing_step:
                existing_step["status"] = status_norm
                if tool_name is not None:
                    existing_step["tool_name"] = tool_name
                if tool_input is not None:
                    existing_step["tool_input"] = tool_input
                if tool_output is not None:
                    existing_step["tool_output"] = tool_output
                if error_message is not None:
                    existing_step["error_message"] = error_message
                if status_norm == "running" and not existing_step.get("started_at"):
                    existing_step["started_at"] = now
                elif status_norm in ("completed", "failed", "skipped"):
                    existing_step["completed_at"] = now
                return dict(existing_step)
            else:
                eff_step_id = (step_id or str(uuid4())).strip()
                record = {
                    "id": eff_step_id,
                    "task_id": task_id.strip(),
                    "user_id": user_id,
                    "step_index": int(step_index),
                    "name": name.strip(),
                    "status": status_norm,
                    "tool_name": tool_name,
                    "tool_input": dict(tool_input or {}) if tool_input is not None else None,
                    "tool_output": tool_output,
                    "error_message": error_message,
                    "started_at": now if status_norm == "running" else None,
                    "completed_at": now if status_norm in ("completed", "failed", "skipped") else None,
                    "created_at": now,
                }
                steps.append(record)
                return dict(record)

    def get_steps(self, task_id: str, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id.strip())
            if not task or task["user_id"] != user_id:
                return []
            steps = list(self._steps.get(task_id.strip(), []))
            steps.sort(key=lambda s: s["step_index"])
            return [dict(s) for s in steps]

    def cancel_task(self, task_id: str, user_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id.strip())
            if not task or task["user_id"] != user_id:
                return False
            if task["status"] in ("pending", "running", "awaiting_approval"):
                task["status"] = "cancelled"
                now = time.time()
                task["completed_at"] = now
                task["updated_at"] = now
                return True
            return False

    def recover_stale_tasks(self, stale_threshold_seconds: float = 300.0) -> list[str]:
        with self._lock:
            now = time.time()
            recovered = []
            for t in self._tasks.values():
                if t["status"] == "running" and (now - t["updated_at"]) > stale_threshold_seconds:
                    t["status"] = "failed"
                    t["error_message"] = "Worker crashed during execution"
                    t["completed_at"] = now
                    t["updated_at"] = now
                    recovered.append(t["id"])
            return recovered


class InMemoryApprovalRepository(BaseApprovalRepository):
    """In-memory thread-safe human approval repository with cryptographic nonce validation."""

    def __init__(self, task_repo: BaseTaskRepository | None = None) -> None:
        self._lock = threading.RLock()
        self._approvals: dict[str, dict[str, Any]] = {}
        self._task_repo = task_repo

    def set_task_repo(self, task_repo: BaseTaskRepository) -> None:
        self._task_repo = task_repo

    def create_approval(
        self,
        task_id: str,
        user_id: str,
        action_type: str,
        action_payload: dict[str, Any],
        justification: str,
        step_id: str | None = None,
        risk_level: str = "medium",
        expires_in_seconds: int = 1800,
        nonce: str | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        import secrets
        with self._lock:
            eff_approval_id = (approval_id or str(uuid4())).strip()
            eff_nonce = nonce.strip() if nonce else secrets.token_urlsafe(32)
            now = time.time()
            record = {
                "id": eff_approval_id,
                "task_id": task_id.strip(),
                "step_id": step_id.strip() if step_id else None,
                "user_id": user_id,
                "action_type": action_type.strip(),
                "action_payload": dict(action_payload or {}),
                "risk_level": risk_level.strip().lower(),
                "justification": justification.strip(),
                "status": "pending",
                "nonce": eff_nonce,
                "decision_reason": None,
                "expires_at": now + float(expires_in_seconds),
                "decided_at": None,
                "created_at": now,
            }
            self._approvals[eff_approval_id] = record
            return dict(record)

    def get_approval(self, approval_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            appr = self._approvals.get(approval_id.strip())
            if not appr or appr["user_id"] != user_id:
                return None
            return dict(appr)

    def list_pending_approvals(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            matched = [
                dict(a) for a in self._approvals.values()
                if a["user_id"] == user_id and a["status"] == "pending" and a["expires_at"] > now
            ]
            matched.sort(key=lambda x: x["created_at"], reverse=True)
            return matched[:limit]

    def get_approval_by_task(
        self,
        task_id: str,
        user_id: str,
        step_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find approval request associated with task and optional step."""
        with self._lock:
            for a in self._approvals.values():
                if a["user_id"] == user_id and a["task_id"] == task_id:
                    if step_id is None or a.get("step_id") == step_id:
                        return dict(a)
            return None

    def decide_approval(
        self,
        approval_id: str,
        user_id: str,
        decision: str,
        nonce: str,
        reason: str = "",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        import secrets
        dec_norm = decision.strip().lower()
        if dec_norm not in ("approved", "rejected"):
            return False, f"Invalid decision '{decision}'. Must be 'approved' or 'rejected'.", None

        with self._lock:
            appr = self._approvals.get(approval_id.strip())
            if not appr or appr["user_id"] != user_id:
                return False, "Approval request not found.", None

            if appr["status"] != "pending":
                return False, f"Approval request has already been decided ({appr['status']}).", dict(appr)

            now = time.time()
            if appr["expires_at"] <= now:
                appr["status"] = "expired"
                return False, "Approval request has expired.", dict(appr)

            if not secrets.compare_digest(appr["nonce"], nonce.strip()):
                return False, "Invalid approval nonce.", None

            appr["status"] = dec_norm
            appr["decision_reason"] = reason.strip()
            appr["decided_at"] = now

            # If task_repo is available, update task state
            if self._task_repo is not None:
                task_id = appr["task_id"]
                if dec_norm == "approved":
                    self._task_repo.update_task_status(task_id, user_id, "pending")
                else:
                    self._task_repo.update_task_status(
                        task_id, user_id, "failed", error_message=f"Approval rejected: {reason.strip()}"
                    )

            return True, f"Approval successfully {dec_norm}.", dict(appr)

    def expire_stale_approvals(self) -> list[str]:
        with self._lock:
            now = time.time()
            expired = []
            for a in self._approvals.values():
                if a["status"] == "pending" and a["expires_at"] <= now:
                    a["status"] = "expired"
                    expired.append(a["id"])
                    if self._task_repo is not None:
                        self._task_repo.update_task_status(
                            a["task_id"], a["user_id"], "timed_out",
                            error_message="Approval request expired without decision"
                        )
            return expired
