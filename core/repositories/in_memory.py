"""M42 — In-Memory Repository Implementations for Project AURA.

Provides fast, thread-safe in-memory repository implementations of all M42 contracts
for unit testing, development environments, and offline validation.
Enforces the same semantic isolation, composite ownership constraints, and token hashing
as the PostgreSQL production implementation.
"""

from __future__ import annotations

import hashlib
import hmac
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
    BaseMemoryRepository,
    BaseUserPreferencesRepository,
    BaseUserRepository,
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
