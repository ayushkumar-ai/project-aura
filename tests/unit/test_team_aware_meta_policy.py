"""
Unit tests for Milestone 22 team-aware meta policy and goal adaptation.
Tests MetaPolicyEngine strategy selection for multi-agent & consensus goals,
GoalAdapter multi-agent team binding synthesis, and GoalReasoner proposed team bindings.
"""

import pytest
from core.goal import Goal
from core.goal_reasoner import GoalReasoner, GoalEvaluationResult
from core.goal_adapter import GoalAdapter, GoalAdaptationResult
from core.meta_policy import MetaPolicyEngine
from core.strategy_types import StrategyType, StrategyAttemptOutcome
from core.strategy_lineage import StrategyLineageStore
from core.team_types import TeamTopology
from core.goal_team_binding import GoalTeamBinding, GoalTeamBindingStatus, sanitize_binding_metadata
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry
from core.provenance import TaintedValue


def test_meta_policy_selects_multi_agent_team_strategy():
    engine = MetaPolicyEngine()
    goal = Goal(
        title="Full Stack System Architecture & Implementation",
        description="Design and build frontend, backend, and security auditing for scalable platform",
        success_criteria=("architecture", "implementation", "security_audit", "review"),
        assigned_team_id="team_engineering_1",
    )
    decision = engine.select_strategy(goal)
    assert decision.selected_strategy == StrategyType.MULTI_AGENT_TEAM_COLLABORATION
    assert decision.confidence > 0.80


def test_meta_policy_selects_consensus_deliberation_strategy():
    engine = MetaPolicyEngine()
    goal = Goal(
        title="Critical Security & Architectural Policy Approval",
        description="Multi-agent committee review, vote and deliberation on compliance standard",
        success_criteria=("voting", "consensus", "deliberation"),
        execution_topology=TeamTopology.CONSENSUS_VOTING,
    )
    decision = engine.select_strategy(goal)
    assert decision.selected_strategy == StrategyType.TEAM_CONSENSUS_DELIBERATION
    assert decision.confidence > 0.80


def test_meta_policy_pivots_from_failing_direct_to_team_collaboration():
    lineage = StrategyLineageStore()
    engine = MetaPolicyEngine(lineage_store=lineage)
    goal = Goal(
        title="Complex Microservices Pipeline",
        description="Complex distributed multi-role execution",
        assigned_team_id="dev_team_1",
    )
    # Record failures of DIRECT_SKILL and DECOMPOSED_HIERARCHICAL
    lineage.record_attempt(
        goal_id=goal.goal_id,
        strategy_type=StrategyType.DIRECT_SKILL,
        outcome=StrategyAttemptOutcome.FAILURE,
        failure_category="complexity",
    )
    lineage.record_attempt(
        goal_id=goal.goal_id,
        strategy_type=StrategyType.DECOMPOSED_HIERARCHICAL,
        outcome=StrategyAttemptOutcome.FAILURE,
        failure_category="role_specialization_needed",
    )
    decision = engine.select_strategy(goal, failure_category="role_specialization_needed")
    assert decision.selected_strategy == StrategyType.MULTI_AGENT_TEAM_COLLABORATION


def test_goal_adapter_synthesizes_team_binding_for_multi_agent_strategy():
    roles = RoleRegistry()
    roles.register(AgentRole(role_id="specialist_ml", name="ML Specialist", description="ML Specialist", system_prompt="You are an ML specialist."))
    meta = MetaPolicyEngine()
    adapter = GoalAdapter(meta_policy=meta, role_registry=roles)

    goal = Goal(
        title="Deploy ML Pipeline",
        description="Multi-agent ML training and verification",
        assigned_team_id="ml_team_alpha",
        assigned_role_id="specialist_ml",
        execution_topology=TeamTopology.SEQUENTIAL_PIPELINE,
    )

    result = adapter.adapt_goal(goal)
    assert isinstance(result, GoalAdaptationResult)
    assert result.selected_strategy == StrategyType.MULTI_AGENT_TEAM_COLLABORATION
    assert result.adapted_team_binding is not None
    assert result.adapted_team_binding.team_id == "ml_team_alpha"
    assert result.adapted_team_binding.topology == TeamTopology.SEQUENTIAL_PIPELINE
    assert result.adapted_team_binding.lead_role_id == "specialist_ml"
    assert "specialist_ml" in result.adapted_team_binding.required_role_ids


def test_goal_adapter_synthesizes_consensus_binding():
    meta = MetaPolicyEngine()
    adapter = GoalAdapter(meta_policy=meta)

    goal = Goal(
        title="Approve Architecture Standard",
        description="Committee voting and review",
        execution_topology=TeamTopology.CONSENSUS_VOTING,
    )

    result = adapter.adapt_goal(goal)
    assert result.selected_strategy == StrategyType.TEAM_CONSENSUS_DELIBERATION
    assert result.adapted_team_binding is not None
    assert result.adapted_team_binding.topology == TeamTopology.CONSENSUS_VOTING


def test_goal_reasoner_evaluates_and_proposes_team_binding():
    reasoner = GoalReasoner()
    goal = Goal(
        title="Multi-agent distributed migration",
        description="Decompose into architect, coder, and auditor workflows",
        success_criteria=("architecture", "implementation", "audit"),
        assigned_team_id="migration_team",
        execution_topology=TeamTopology.HIERARCHICAL,
    )
    result = reasoner.evaluate(goal)
    assert isinstance(result, GoalEvaluationResult)
    assert result.proposed_team_binding is not None
    assert result.proposed_team_binding.team_id == "migration_team"
    assert result.proposed_team_binding.topology == TeamTopology.HIERARCHICAL
    assert result.proposed_team_binding.status == GoalTeamBindingStatus.BOUND


def test_sanitize_binding_metadata_security_and_taint():
    raw_meta = {
        "is_admin": True,
        "is_authorized": True,
        "approved": True,
        "bypass_policy": True,
        "sudo": True,
        "dataset_name": "production_users",
        "tainted_payload": TaintedValue(raw_value="untrusted_source_code", source_type="web_crawler"),
    }
    sanitized = sanitize_binding_metadata(raw_meta)
    assert "is_admin" not in sanitized
    assert "is_authorized" not in sanitized
    assert "approved" not in sanitized
    assert "bypass_policy" not in sanitized
    assert "sudo" not in sanitized
    assert sanitized["dataset_name"] == "production_users"
    assert isinstance(sanitized["tainted_payload"], TaintedValue)
    assert sanitized["tainted_payload"].value == "untrusted_source_code"
    assert sanitized["tainted_payload"].source_type == "web_crawler"
