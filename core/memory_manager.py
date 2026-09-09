import copy
import json
import logging
import time
from typing import Any
from uuid import uuid4

from app.config import settings
from core.agent_memory import (
    AgentMemoryStore,
    InMemoryAgentMemoryStore,
)
from core.memory_types import (
    EpisodicRecord,
    MemoryEntry,
    MemoryNamespace,
    MemoryTier,
    SemanticFact,
)
from core.provenance import (
    TaintedValue,
    is_tainted,
    render_for_prompt,
    unwrap_tainted,
    wrap_tainted,
)

logger = logging.getLogger("aura.memory_manager")


class MemoryManager:
    """Single unified orchestration interface for multi-tier agent memory."""

    def __init__(
        self,
        store: AgentMemoryStore | None = None,
        max_working_entries: int | None = None,
        max_semantic_facts: int | None = None,
        max_episodic_records: int | None = None,
        max_search_results: int | None = None,
        max_fact_chars: int | None = None,
    ):
        self.config = settings
        self.max_working_entries = (
            max_working_entries
            if max_working_entries is not None
            else getattr(settings, "aura_max_working_memory_entries", 50)
        )
        self.max_semantic_facts = (
            max_semantic_facts
            if max_semantic_facts is not None
            else getattr(settings, "aura_max_semantic_facts", 200)
        )
        self.max_episodic_records = (
            max_episodic_records
            if max_episodic_records is not None
            else getattr(settings, "aura_max_episodic_records", 500)
        )
        self.max_search_results = (
            max_search_results
            if max_search_results is not None
            else getattr(settings, "aura_max_memory_search_results", 5)
        )
        self.max_fact_chars = (
            max_fact_chars
            if max_fact_chars is not None
            else getattr(settings, "aura_max_memory_fact_chars", 4000)
        )

        if store is not None:
            if not isinstance(store, AgentMemoryStore):
                raise TypeError("store must be an instance of AgentMemoryStore.")
            self.store = store
        else:
            self.store = InMemoryAgentMemoryStore(
                max_working_entries=self.max_working_entries,
                max_semantic_facts=self.max_semantic_facts,
                max_episodic_records=self.max_episodic_records,
            )

    # ---------------------------------------------------------
    # Working Memory Operations (Task-Scoped Scratchpad)
    # ---------------------------------------------------------
    def write_working_fact(
        self,
        task_id: str,
        key: str,
        value: Any,
        is_untrusted: bool = False,
        source_urls: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Write an intermediate working fact into task-scoped scratchpad."""
        clean_task_id = str(task_id).strip()
        if not clean_task_id:
            raise ValueError("task_id must be a non-empty string.")

        entry = MemoryEntry(
            tier=MemoryTier.WORKING,
            namespace=f"task:{clean_task_id}",
            key=key,
            value=value,
            is_untrusted=is_untrusted,
            source_urls=tuple(source_urls),
            metadata=dict(metadata or {}),
        )
        return self.store.store(entry)

    def read_working_fact(self, task_id: str, key: str, default: Any = None) -> Any:
        """Read a working fact from task-scoped scratchpad."""
        clean_task_id = str(task_id).strip()
        if not clean_task_id:
            return default
        entry = self.store.get_by_key(
            tier=MemoryTier.WORKING,
            namespace=f"task:{clean_task_id}",
            key=key,
        )
        if entry is None:
            return default
        return entry.value

    def list_working_facts(self, task_id: str) -> dict[str, Any]:
        """List all working facts stored for a given task ID."""
        clean_task_id = str(task_id).strip()
        if not clean_task_id:
            return {}
        entries = self.store.list_entries(
            tier=MemoryTier.WORKING,
            namespace=f"task:{clean_task_id}",
        )
        return {e.key: e.value for e in entries}

    def clear_working_memory(self, task_id: str) -> None:
        """Clear all working scratchpad memory for a task ID."""
        clean_task_id = str(task_id).strip()
        if clean_task_id:
            self.store.clear_tier(
                tier=MemoryTier.WORKING,
                namespace=f"task:{clean_task_id}",
            )

    # Working memory aliases
    def set_working(self, scope: str, key: str, value: Any, is_untrusted: bool = False, **kwargs) -> MemoryEntry:
        return self.write_working_fact(task_id=scope, key=key, value=value, is_untrusted=is_untrusted, **kwargs)

    def get_working(self, scope: str, key: str, default: Any = None) -> Any:
        return self.read_working_fact(task_id=scope, key=key, default=default)

    def get_all_working(self, scope: str) -> dict[str, Any]:
        return self.list_working_facts(task_id=scope)

    def clear_working(self, scope: str) -> None:
        self.clear_working_memory(task_id=scope)

    # ---------------------------------------------------------
    # Semantic Memory Operations (Durable Facts & Preferences)
    # ---------------------------------------------------------
    def store_fact(
        self,
        subject: str,
        predicate: str,
        object_value: Any,
        confidence: float = 1.0,
        is_untrusted: bool = False,
        namespace: str | MemoryNamespace = MemoryNamespace.USER_PROFILE,
        source_urls: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Store a structured semantic fact."""
        fact = SemanticFact(
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            confidence=confidence,
            is_untrusted=is_untrusted,
            source_urls=tuple(source_urls),
            metadata=dict(metadata or {}),
        )
        entry = fact.to_memory_entry(namespace=namespace)
        return self.store.store(entry)

    def add_fact(
        self,
        subject: str,
        predicate: str,
        object_val: Any = None,
        object_value: Any = None,
        confidence: float = 1.0,
        namespace: str | MemoryNamespace = MemoryNamespace.USER_PROFILE,
        provenance: Any | None = None,
        source_urls: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        is_untrusted: bool = False,
    ) -> SemanticFact:
        """Convenience method to add a semantic fact returning SemanticFact model."""
        actual_obj = object_value if object_value is not None else object_val
        if is_tainted(actual_obj):
            if not getattr(actual_obj, "is_trusted", True):
                is_untrusted = True
        if provenance is not None:
            if hasattr(provenance, "is_trusted") and not provenance.is_trusted:
                is_untrusted = True
            elif hasattr(provenance, "is_untrusted") and provenance.is_untrusted:
                is_untrusted = True

        meta = dict(metadata or {})
        if provenance is not None:
            if hasattr(provenance, "to_dict"):
                meta["provenance"] = provenance.to_dict()
            else:
                meta["provenance"] = str(provenance)

        fact = SemanticFact(
            subject=subject,
            predicate=predicate,
            object_value=actual_obj,
            confidence=confidence,
            is_untrusted=is_untrusted,
            source_urls=tuple(source_urls),
            metadata=meta,
        )
        entry = fact.to_memory_entry(namespace=namespace)
        stored_entry = self.store.store(entry)
        fact.id = stored_entry.entry_id
        return fact

    def get_fact(
        self,
        fact_id_or_subject: str,
        predicate: str | None = None,
        namespace: str | MemoryNamespace = MemoryNamespace.USER_PROFILE,
    ) -> SemanticFact | None:
        """Retrieve a semantic fact by ID or by (subject, predicate)."""
        if predicate is None:
            # Look up by ID
            entry = self.store.get_by_id(fact_id_or_subject)
            if entry is None or entry.tier != MemoryTier.SEMANTIC:
                return None
            return SemanticFact.from_memory_entry(entry)
        else:
            ns = namespace.value if isinstance(namespace, MemoryNamespace) else namespace
            key = f"{str(fact_id_or_subject).strip()}:{str(predicate).strip()}"
            entry = self.store.get_by_key(
                tier=MemoryTier.SEMANTIC,
                namespace=ns,
                key=key,
            )
            if entry is None:
                return None
            return SemanticFact.from_memory_entry(entry)

    def delete_fact(self, fact_id: str) -> bool:
        """Delete a semantic fact by its ID."""
        try:
            self.store.delete(fact_id)
            return True
        except Exception:
            return False

    def store_user_preference(
        self,
        key: str,
        value: Any,
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """Store a user profile preference."""
        entry = MemoryEntry(
            tier=MemoryTier.SEMANTIC,
            namespace=MemoryNamespace.USER_PROFILE.value,
            key=key,
            value=value,
            confidence=confidence,
            is_untrusted=False,
        )
        return self.store.store(entry)

    def get_user_preference(self, key: str) -> Any | None:
        """Retrieve a user preference value."""
        entry = self.store.get_by_key(
            tier=MemoryTier.SEMANTIC,
            namespace=MemoryNamespace.USER_PROFILE.value,
            key=key,
        )
        return entry.value if entry is not None else None

    def search_facts(
        self,
        query: str,
        namespace: str | None = None,
        top_k: int | None = None,
    ) -> list[SemanticFact]:
        """Search semantic facts matching a query."""
        limit = top_k if top_k is not None else self.max_search_results
        entries = self.store.search(
            query=query,
            tier=MemoryTier.SEMANTIC,
            namespace=namespace,
            top_k=limit,
        )
        facts: list[SemanticFact] = []
        for e in entries:
            try:
                facts.append(SemanticFact.from_memory_entry(e))
            except Exception:
                pass
        return facts

    # ---------------------------------------------------------
    # Episodic Memory Operations (Task Traces & Experience)
    # ---------------------------------------------------------
    def record_episode(
        self,
        task_id: str,
        plan_id: str = "",
        task_goal: str = "",
        objective: str = "",
        plan_summary: str = "",
        outcome_status: str = "COMPLETED",
        success: bool | None = None,
        executed_skills: list[str] | tuple[str, ...] = (),
        key_learnings: list[str] | tuple[str, ...] = (),
        error: str | None = None,
        execution_time_ms: float = 0.0,
        trace_summary: dict[str, Any] | None = None,
        is_untrusted: bool = False,
        source_urls: list[str] | tuple[str, ...] = (),
        provenance: Any | None = None,
        metadata: dict[str, Any] | None = None,
        goal_id: str = "",
    ) -> EpisodicRecord:
        """Record a completed task episode for historical reference."""
        actual_goal = task_goal if task_goal else objective
        actual_plan = plan_id if plan_id else (goal_id if goal_id else "")
        actual_success = success if success is not None else (outcome_status.upper() in ("COMPLETED", "SUCCESS", "SUCCEEDED"))

        meta = dict(metadata or {})
        if provenance is not None:
            if hasattr(provenance, "to_dict"):
                meta["provenance"] = provenance.to_dict()
            else:
                meta["provenance"] = str(provenance)
            if hasattr(provenance, "is_trusted") and not provenance.is_trusted:
                is_untrusted = True
            elif hasattr(provenance, "is_untrusted") and provenance.is_untrusted:
                is_untrusted = True
        if goal_id:
            meta["goal_id"] = goal_id
        if plan_summary:
            meta["plan_summary"] = plan_summary
        if key_learnings:
            meta["key_learnings"] = list(key_learnings)

        record = EpisodicRecord(
            task_id=task_id,
            plan_id=actual_plan,
            task_goal=actual_goal,
            success=actual_success,
            executed_skills=tuple(executed_skills),
            error=error,
            execution_time_ms=execution_time_ms,
            trace_summary=dict(trace_summary or {}),
            is_untrusted=is_untrusted,
            source_urls=tuple(source_urls),
            metadata=meta,
        )
        entry = record.to_memory_entry()
        stored_entry = self.store.store(entry)
        record.id = stored_entry.entry_id
        return record

    def get_recent_episodes(self, limit: int = 5) -> list[EpisodicRecord]:
        """Get recent episodic execution records."""
        entries = self.store.list_entries(
            tier=MemoryTier.EPISODIC,
            namespace=MemoryNamespace.EXECUTION_HISTORY.value,
        )
        # Sort by timestamp desc
        sorted_entries = sorted(entries, key=lambda x: x.created_at, reverse=True)[:limit]
        records: list[EpisodicRecord] = []
        for e in sorted_entries:
            try:
                records.append(EpisodicRecord.from_memory_entry(e))
            except Exception:
                pass
        return records

    def recall_episodes(
        self,
        task_description: str,
        top_k: int | None = None,
    ) -> list[EpisodicRecord]:
        """Search and recall relevant past execution episodes for a task description."""
        limit = top_k if top_k is not None else self.max_search_results
        entries = self.store.search(
            query=task_description,
            tier=MemoryTier.EPISODIC,
            namespace=MemoryNamespace.EXECUTION_HISTORY.value,
            top_k=limit,
        )
        records: list[EpisodicRecord] = []
        for e in entries:
            try:
                records.append(EpisodicRecord.from_memory_entry(e))
            except Exception:
                pass
        return records

    def search_episodes(self, query: str, top_k: int | None = None) -> list[EpisodicRecord]:
        return self.recall_episodes(task_description=query, top_k=top_k)

    # ---------------------------------------------------------
    # Prompt Context Builder (Safe Taint Isolation)
    # ---------------------------------------------------------
    def build_memory_context_prompt(
        self,
        query: str,
        task_id: str | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Construct a bounded, taint-isolated memory context block for LLM prompts."""
        limit = top_k if top_k is not None else self.max_search_results
        char_cap = max_chars if max_chars is not None else self.max_fact_chars
        sections: list[str] = []

        # 1. Semantic Facts / User Preferences
        semantic_facts = self.search_facts(query, top_k=limit)
        if semantic_facts:
            fact_lines: list[str] = []
            for f in semantic_facts:
                val_raw = f.object_val
                val_rendered = render_for_prompt(val_raw, wrap_untrusted=f.is_untrusted or is_tainted(val_raw))
                if len(val_rendered) > char_cap:
                    val_rendered = val_rendered[: char_cap - 3] + "..."
                fact_lines.append(f"- Fact: {f.subject} {f.predicate} -> {val_rendered}")
            sections.append("Relevant Facts & Preferences:\n" + "\n".join(fact_lines))

        # 2. Task Working Memory (if task_id provided)
        if task_id:
            working_facts = self.list_working_facts(task_id)
            if working_facts:
                work_lines: list[str] = []
                for k, v in sorted(working_facts.items()):
                    val_rendered = render_for_prompt(v, wrap_untrusted=is_tainted(v))
                    if len(val_rendered) > char_cap:
                        val_rendered = val_rendered[: char_cap - 3] + "..."
                    work_lines.append(f"- {k}: {val_rendered}")
                sections.append("Working Scratchpad Context:\n" + "\n".join(work_lines))

        # 3. Episodic Past Experience
        episodes = self.recall_episodes(query, top_k=limit)
        if episodes:
            ep_lines: list[str] = []
            for ep in episodes:
                status_str = "SUCCEEDED" if ep.success else "FAILED"
                skills_str = ", ".join(ep.executed_skills) if ep.executed_skills else "none"
                line = f"- Task: '{ep.task_goal}' -> Status: {status_str} (Skills: {skills_str})"
                if not ep.success and ep.error:
                    line += f" | Failure cause: {ep.error}"
                ep_lines.append(line)
            sections.append("Past Experience / Similar Tasks:\n" + "\n".join(ep_lines))

        if not sections:
            return ""

        full_text = "\n\n".join(sections)
        if max_chars is not None and len(full_text) > max_chars:
            full_text = full_text[: max_chars - 3] + "..."
        return full_text

    def build_prompt_context(
        self,
        query: str,
        task_id: str | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Alias for build_memory_context_prompt."""
        return self.build_memory_context_prompt(
            query=query,
            task_id=task_id,
            top_k=top_k,
            max_chars=max_chars,
        )
