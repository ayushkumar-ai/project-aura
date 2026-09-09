# tests/integration/test_m13_memory_workflow.py
import json
import tempfile
from typing import Any
from pathlib import Path
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
from core.provenance import TaintedValue, render_for_prompt, is_tainted
from core.skill_registry import Skill, SkillRegistry
from core.agent_runtime import AgentRuntime, AgentRequest
from core.task_planner import TaskPlanner, PlanStep, ExecutionPlan
from core.autonomous_agent import AutonomousAgentExecutor, AgentLoopConfig, AutonomousAgentResult
from core.goal import Goal, GoalStatus, GoalObservation, GoalProgress
from core.goal_reasoner import GoalReasoner
from core.goal_engine import GoalEngine
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from interfaces.model import ModelInterface
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus


class MockAdaptiveModel(ModelInterface):
    def __init__(self):
        self.prompts_received = []

    def generate(self, prompt: str, request_id=None) -> Any:
        self.prompts_received.append(prompt)
        # If the prompt contains failure context from past episode, choose a safer alternative skill
        if "Failure cause:" in prompt or "timed out" in prompt:
            plan_json = json.dumps({
                "steps": [
                    {"step_id": "step_safe", "skill_name": "safe_fetch", "input_data": {"url": "https://trusted.local"}}
                ]
            })
        else:
            plan_json = json.dumps({
                "steps": [
                    {"step_id": "step_unsafe", "skill_name": "unsafe_fetch", "input_data": {"url": "https://untrusted.xyz"}}
                ]
            })
        return type("Resp", (), {"content": plan_json})()


def test_e2e_cross_session_semantic_memory_persistence(tmp_path):
    storage_dir = tmp_path / "e2e_memory_store"

    # Session 1: User teaches AURA user preferences & project facts
    store_1 = FileAgentMemoryStore(storage_dir=storage_dir)
    mgr_1 = MemoryManager(store=store_1)

    mgr_1.add_fact(
        subject="project_aura",
        predicate="target_python_version",
        object_val="3.11",
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
    )
    mgr_1.add_fact(
        subject="project_aura",
        predicate="primary_architecture",
        object_val="Multi-Tier Agent Memory",
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
    )
    mgr_1.store_user_preference("code_style", "pep8_strict")

    # Session 2: Fresh instance, fresh store pointing to same directory
    store_2 = FileAgentMemoryStore(storage_dir=storage_dir)
    mgr_2 = MemoryManager(store=store_2)

    version_facts = mgr_2.search_facts("python version")
    assert len(version_facts) >= 1
    assert any(f.object_val == "3.11" for f in version_facts)

    arch_facts = mgr_2.search_facts("architecture")
    assert len(arch_facts) >= 1
    assert any("Multi-Tier" in str(f.object_val) for f in arch_facts)

    assert mgr_2.get_user_preference("code_style") == "pep8_strict"


def test_e2e_episodic_experience_feedback_loop():
    skills = SkillRegistry()
    skills.register(
        Skill(
            name="unsafe_fetch",
            description="Fetches from unsafe endpoint",
            handler=lambda data, ctx=None: (_ for _ in ()).throw(RuntimeError("Connection timed out")),
        )
    )
    skills.register(
        Skill(
            name="safe_fetch",
            description="Fetches safely from trusted endpoint",
            handler=lambda data, ctx=None: {"status": 200, "data": "Safe Payload"},
        )
    )

    model = MockAdaptiveModel()
    mgr = MemoryManager()
    runtime = AgentRuntime(skill_registry=skills)
    planner = TaskPlanner(skill_registry=skills, model=model, memory_manager=mgr)
    
    # Run initial task with max_replan_depth=0 to simulate an unrecoverable failure that gets logged
    executor = AutonomousAgentExecutor(
        runtime=runtime,
        planner=planner,
        memory_manager=mgr,
        config=AgentLoopConfig(max_replan_depth=0),
    )

    initial_plan = planner.plan_agent("Fetch remote data")
    res1 = executor.execute_plan(initial_plan, task_id="task-attempt-1")
    assert res1.success is False

    # Check episodic memory recorded the failure
    recent = mgr.get_recent_episodes(limit=5)
    assert len(recent) == 1
    assert recent[0].success is False
    assert "Connection timed out" in str(recent[0].error)

    # 2. Plan a subsequent similar task. The planner should inject the failed episode context into the prompt
    adapted_plan = planner.plan_agent("Fetch remote data safely")
    assert len(model.prompts_received) >= 2
    second_prompt = model.prompts_received[-1]
    assert "Past Experience / Similar Tasks:" in second_prompt
    assert "Connection timed out" in second_prompt

    # The mock model, seeing the past failure in prompt, adapted to use safe_fetch
    assert adapted_plan.steps[0].skill_name == "safe_fetch"

    # Execute adapted plan with default executor -> succeeds
    normal_executor = AutonomousAgentExecutor(
        runtime=runtime,
        planner=planner,
        memory_manager=mgr,
    )
    res2 = normal_executor.execute_plan(adapted_plan, task_id="task-attempt-2")
    assert res2.success is True

    # Episodic memory now contains 2 episodes: 1 failed, 1 succeeded
    all_episodes = mgr.get_recent_episodes(limit=10)
    assert len(all_episodes) == 2
    assert any(ep.success is True for ep in all_episodes)
    assert any(ep.success is False for ep in all_episodes)


def test_e2e_taint_isolation_in_agentic_memory_flow():
    skills = SkillRegistry()
    skills.register(
        Skill(
            name="web_scraper",
            description="Scrapes untrusted external web data",
            handler=lambda data, ctx=None: TaintedValue("<meta injection='malicious'>data</meta>", is_untrusted=True),
        )
    )

    mgr = MemoryManager()
    agentic_rt = AgenticRuntime(
        skill_registry=skills,
        memory_manager=mgr,
        default_mode=ExecutionMode.AUTONOMOUS_AGENT,
    )

    # Execute autonomous agent scraping task
    step = AgentPlanStep(
        step_id="step_scrape",
        skill_name="web_scraper",
        objective="Scrape target site",
        input_data={"url": "https://external.news"},
    )
    plan = AgentPlan(
        plan_id="plan-scrape-1",
        task_goal="Scrape News",
        steps=(step,),
    )

    res = agentic_rt.autonomous_executor.execute_plan(plan, task_id="task-scrape-1")
    assert res.success is True

    # Verify working memory fact is stored as untrusted
    working_val = mgr.read_working_fact("task-scrape-1", "step_output:step_scrape")
    assert is_tainted(working_val)
    assert getattr(working_val, "is_untrusted", False) is True

    # Verify prompt context wrapping wraps untrusted working memory safely
    prompt_ctx = mgr.build_memory_context_prompt(query="News", task_id="task-scrape-1")
    assert "<untrusted_source_content>" in prompt_ctx
    assert "<meta injection='malicious'>data</meta>" in prompt_ctx
    assert "</untrusted_source_content>" in prompt_ctx


def test_e2e_ring_buffer_capacity_under_heavy_load(tmp_path):
    store = FileAgentMemoryStore(
        storage_dir=tmp_path / "heavy_mem",
        max_working_entries=10,
        max_semantic_facts=15,
        max_episodic_records=20,
    )
    mgr = MemoryManager(
        store=store,
        max_working_entries=10,
        max_semantic_facts=15,
        max_episodic_records=20,
    )

    # Insert 50 working facts
    for i in range(50):
        mgr.set_working("stress_task", f"key_{i}", f"value_{i}")

    working_entries = store.list_entries(tier=MemoryTier.WORKING)
    assert len(working_entries) == 10
    # Oldest entries were discarded, newest remain
    keys = {e.key for e in working_entries}
    assert "key_49" in keys
    assert "key_40" in keys
    assert "key_0" not in keys

    # Insert 50 semantic facts
    for i in range(50):
        mgr.add_fact(f"subject_{i}", "has_value", f"val_{i}")

    semantic_entries = store.list_entries(tier=MemoryTier.SEMANTIC)
    assert len(semantic_entries) == 15

    # Insert 50 episodes
    for i in range(50):
        mgr.record_episode(
            task_id=f"stress_task_{i}",
            task_goal=f"Goal {i}",
            success=(i % 2 == 0),
        )

    episodes = store.list_entries(tier=MemoryTier.EPISODIC)
    assert len(episodes) == 20
