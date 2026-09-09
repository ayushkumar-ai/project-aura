# tests/unit/test_memory_manager_m13.py
import pytest
from core.memory_types import (
    MemoryTier,
    MemoryNamespace,
    MemoryEntry,
    SemanticFact,
    EpisodicRecord,
)
from core.agent_memory import (
    InMemoryAgentMemoryStore,
    FileAgentMemoryStore,
)
from core.memory_manager import MemoryManager
from core.provenance import TaintedValue, render_for_prompt
from app.config import settings, Settings


def test_memory_manager_init():
    mgr = MemoryManager()
    assert mgr.store is not None
    assert mgr.config is not None


def test_working_memory_lifecycle():
    mgr = MemoryManager()
    mgr.set_working("task-123", "current_step", 3)
    mgr.set_working("task-123", "candidate_tools", ["web_search", "summarize"])

    assert mgr.get_working("task-123", "current_step") == 3
    assert mgr.get_working("task-123", "candidate_tools") == ["web_search", "summarize"]
    assert mgr.get_working("task-123", "nonexistent") is None
    assert mgr.get_working("task-123", "nonexistent", default="fallback") == "fallback"

    all_w = mgr.get_all_working("task-123")
    assert all_w == {
        "current_step": 3,
        "candidate_tools": ["web_search", "summarize"],
    }

    # Different scope isolation
    mgr.set_working("task-456", "current_step", 1)
    assert mgr.get_working("task-456", "current_step") == 1
    assert mgr.get_working("task-123", "current_step") == 3

    # Clear scope
    mgr.clear_working("task-123")
    assert mgr.get_all_working("task-123") == {}
    assert mgr.get_working("task-456", "current_step") == 1


def test_semantic_memory_operations():
    mgr = MemoryManager()
    fact1 = mgr.add_fact(
        subject="user_preference",
        predicate="prefers_language",
        object_val="Python",
        confidence=0.95,
        namespace=MemoryNamespace.USER_PROFILE,
    )
    assert fact1.subject == "user_preference"
    assert fact1.object_val == "Python"
    assert fact1.confidence == 0.95

    # Retrieve
    retrieved = mgr.get_fact(fact1.id)
    assert retrieved is not None
    assert retrieved.object_val == "Python"

    # Search facts
    results = mgr.search_facts("Python language preference")
    assert len(results) >= 1
    assert any(f.object_val == "Python" for f in results)

    # Delete fact
    deleted = mgr.delete_fact(fact1.id)
    assert deleted is True
    assert mgr.get_fact(fact1.id) is None


def test_episodic_memory_operations():
    mgr = MemoryManager()
    record = mgr.record_episode(
        task_id="task-789",
        goal_id="goal-456",
        objective="Analyze codebase architecture",
        plan_summary="Inspected M1-M12 modules and produced summary",
        outcome_status="COMPLETED",
        key_learnings=["Architecture is clean", "Memory tier needed"],
    )
    assert record.task_id == "task-789"
    assert record.metadata.get("goal_id") == "goal-456"
    assert record.success is True

    recent = mgr.get_recent_episodes(limit=5)
    assert len(recent) == 1
    assert recent[0].task_id == "task-789"

    search_res = mgr.search_episodes("Analyze codebase")
    assert len(search_res) >= 1
    assert search_res[0].task_id == "task-789"


def test_taint_isolation_in_prompt_context():
    mgr = MemoryManager()

    # Trusted fact
    mgr.add_fact(
        subject="safe_info",
        predicate="is",
        object_val="Verified Fact",
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
    )

    # Untrusted fact
    untrusted_val = TaintedValue("External Untrusted Web Fact", is_untrusted=True)
    mgr.add_fact(
        subject="web_snippet",
        predicate="content",
        object_val=untrusted_val,
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
        is_untrusted=True,
    )

    context = mgr.build_prompt_context(query="Fact")
    assert "Verified Fact" in context
    assert "<untrusted_source_content>" in context
    assert "External Untrusted Web Fact" in context
    assert "</untrusted_source_content>" in context


def test_prompt_context_size_bounding():
    mgr = MemoryManager()
    long_text = "X" * 3000
    mgr.add_fact(
        subject="big_data",
        predicate="data",
        object_val=long_text,
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
    )

    context = mgr.build_prompt_context(query="big_data", max_chars=500)
    assert len(context) <= 600


def test_file_backed_memory_manager(tmp_path):
    store = FileAgentMemoryStore(str(tmp_path / "memory_mgr_test"))
    mgr = MemoryManager(store=store)

    mgr.set_working("scope_a", "k1", "v1")
    f = mgr.add_fact("sub", "pred", "obj")
    e = mgr.record_episode("t1", plan_id="p1", task_goal="do work", success=True)

    # Verify persistence by instantiating new store & manager pointing to same path
    new_store = FileAgentMemoryStore(str(tmp_path / "memory_mgr_test"))
    new_mgr = MemoryManager(store=new_store)

    assert new_mgr.get_working("scope_a", "k1") == "v1"
    assert new_mgr.get_fact(f.id) is not None
    assert len(new_mgr.get_recent_episodes()) == 1
