# tests/unit/test_agent_memory_integration_m13.py
from typing import Any
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
from core.skill_registry import Skill, SkillRegistry
from core.agent_runtime import AgentRuntime, AgentRequest
from core.task_planner import TaskPlanner, PlanStep, ExecutionPlan, ReplanContext
from core.autonomous_agent import AutonomousAgentExecutor, AgentLoopConfig
from core.goal import Goal, GoalStatus, GoalObservation
from core.goal_reasoner import GoalReasoner
from core.goal_engine import GoalEngine
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from interfaces.model import ModelInterface
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus


class FakePromptRecordingModel(ModelInterface):
    def __init__(self, responses=None):
        self.prompts_received = []
        self.responses = responses or []
        self.call_count = 0

    def generate(self, prompt: str, request_id=None) -> Any:
        self.prompts_received.append(prompt)
        if self.responses and self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return type("Resp", (), {"content": resp})()
        # Default mock plan
        default_json = '{"steps": [{"step_id": "step_1", "skill_name": "calc", "input_data": {"val": 42}}]}'
        return type("Resp", (), {"content": default_json})()


def test_task_planner_memory_context_injection():
    skills = SkillRegistry()
    skills.register(Skill(name="calc", description="Performs calculation"))

    mgr = MemoryManager()
    mgr.add_fact(
        subject="calculation",
        predicate="format_output_as",
        object_val="JSON_TABLE",
        namespace=MemoryNamespace.USER_PROFILE,
    )
    mgr.record_episode(
        task_id="task-old",
        task_goal="Previous calculation task",
        success=True,
        executed_skills=["calc"],
    )

    model = FakePromptRecordingModel()
    planner = TaskPlanner(skill_registry=skills, model=model, memory_manager=mgr)

    plan = planner.plan_agent(task="Perform calculation task")
    assert plan is not None
    assert len(model.prompts_received) == 1
    last_prompt = model.prompts_received[0]
    assert "Relevant Memory & Context:" in last_prompt
    assert "calculation" in last_prompt
    assert "JSON_TABLE" in last_prompt
    assert "Previous calculation task" in last_prompt


def test_autonomous_agent_working_and_episodic_integration():
    skills = SkillRegistry()
    skills.register(
        Skill(
            name="echo",
            description="Echoes input",
            handler=lambda data, ctx=None: data,
        )
    )

    runtime = AgentRuntime(skill_registry=skills)
    mgr = MemoryManager()
    planner = TaskPlanner(skill_registry=skills)

    executor = AutonomousAgentExecutor(
        runtime=runtime,
        planner=planner,
        memory_manager=mgr,
    )

    step = AgentPlanStep(
        step_id="step_echo",
        skill_name="echo",
        objective="echo greeting",
        input_data={"msg": "Hello AURA Memory"},
    )
    plan = AgentPlan(
        plan_id="plan-mem-1",
        task_goal="Test Memory Recording",
        steps=(step,),
    )

    res = executor.execute_plan(plan=plan, task_id="task-mem-run-1")
    assert res.success is True

    # 1. Verify working memory captured step output
    step_out = mgr.read_working_fact("task-mem-run-1", "step_output:step_echo")
    assert step_out == {"msg": "Hello AURA Memory"}

    # 2. Verify episodic memory recorded the run
    recent_episodes = mgr.get_recent_episodes(limit=5)
    assert len(recent_episodes) == 1
    ep = recent_episodes[0]
    assert ep.task_id == "task-mem-run-1"
    assert ep.task_goal == "Test Memory Recording"
    assert ep.success is True
    assert "echo" in ep.executed_skills


def test_goal_reasoner_utilizes_semantic_memory():
    skills = SkillRegistry()
    skills.register(Skill(name="report", description="Generate report"))

    mgr = MemoryManager()
    mgr.add_fact(
        subject="quarterly_report",
        predicate="status",
        object_val="published",
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE,
    )

    reasoner = GoalReasoner(skill_registry=skills, memory_manager=mgr)
    goal = Goal(
        title="Publish Quarterly Report",
        description="Ensure quarterly report published",
        success_criteria=("quarterly_report status published",),
    )

    eval_res = reasoner.evaluate(goal)
    assert eval_res.is_completed is True
    assert eval_res.new_progress.percentage == 1.0


def test_agentic_runtime_propagates_memory_manager():
    mgr = MemoryManager()
    skills = SkillRegistry()
    skills.register(Skill(name="tool_a", description="Executes tool A", handler=lambda r: "Done A"))

    runtime = AgenticRuntime(
        skill_registry=skills,
        memory_manager=mgr,
    )

    assert runtime.memory_manager is mgr
    assert runtime.planner.memory_manager is mgr
    assert runtime.autonomous_executor.memory_manager is mgr
    assert runtime.goal_engine.memory_manager is mgr


def test_memory_security_invariants_preserved():
    mgr = MemoryManager()

    # Attempt to inject permission keys in metadata
    tampered_entry = mgr.add_fact(
        subject="admin_user",
        predicate="role",
        object_val="superuser",
        metadata={
            "approved": True,
            "auto_approve": True,
            "permission": "all",
            "safe_tag": "valid_meta",
        },
    )

    # Verify forbidden keys were stripped
    stored = mgr.get_fact(tampered_entry.id)
    assert "approved" not in stored.metadata
    assert "auto_approve" not in stored.metadata
    assert "permission" not in stored.metadata
    assert stored.metadata.get("safe_tag") == "valid_meta"

    # Untrusted data is wrapped with isolation in prompt
    untrusted_val = TaintedValue("<script>alert(1)</script>", is_untrusted=True)
    mgr.add_fact(
        subject="web_payload",
        predicate="data",
        object_val=untrusted_val,
        is_untrusted=True,
    )

    prompt_ctx = mgr.build_memory_context_prompt(query="payload")
    assert "<untrusted_source_content>" in prompt_ctx
    assert "<script>alert(1)</script>" in prompt_ctx
    assert "</untrusted_source_content>" in prompt_ctx
