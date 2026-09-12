"""Unit tests for M35 Proactive Assistance Engine Subsystem."""

from core.proactive_engine import ProactiveAssistanceEngine
from core.proactive_types import (
    ProactiveActionType,
    ProposalStatus,
    TriggerDefinition,
    TriggerType,
)


def test_proactive_engine_initialization_defaults():
    engine = ProactiveAssistanceEngine()
    triggers = engine.list_triggers()
    assert len(triggers) >= 2
    trig_ids = {t.trigger_id for t in triggers}
    assert "trig_stale_goals" in trig_ids
    assert "trig_periodic_cleanup" in trig_ids


def test_trigger_evaluation_and_proposal_creation():
    engine = ProactiveAssistanceEngine()
    # State has stale goals
    state = {"stale_goals_count": 3}
    proposals = engine.evaluate_triggers(current_state=state, now=1000.0)

    # trig_stale_goals should fire and generate a pending proposal
    stale_props = [p for p in proposals if p.trigger_id == "trig_stale_goals"]
    assert len(stale_props) == 1
    assert stale_props[0].status == ProposalStatus.PENDING_APPROVAL
    assert stale_props[0].payload["action"] == "replan_stale_goals"


def test_cooldown_anti_spam_enforcement():
    engine = ProactiveAssistanceEngine()
    state = {"stale_goals_count": 1}

    # First evaluation at t=1000
    p1 = engine.evaluate_triggers(current_state=state, now=1000.0)
    assert len(p1) >= 1

    # Second evaluation within cooldown period at t=1030 (cooldown is 120s)
    p2 = engine.evaluate_triggers(current_state=state, now=1030.0)
    assert len(p2) == 0

    # Third evaluation after cooldown expiry at t=1150
    p3 = engine.evaluate_triggers(current_state=state, now=1150.0)
    assert len(p3) >= 1


def test_approve_and_reject_proposal():
    engine = ProactiveAssistanceEngine()
    state = {"stale_goals_count": 2}
    proposals = engine.evaluate_triggers(current_state=state, now=1000.0)
    prop = next(p for p in proposals if p.trigger_id == "trig_stale_goals")

    # Approve
    approved = engine.approve_proposal(prop.proposal_id)
    assert approved.status == ProposalStatus.EXECUTED
    assert approved.user_decision == "approved"

    # Audit check
    audit = engine.get_audit_log()
    assert len(audit) >= 2


def test_reject_proposal():
    engine = ProactiveAssistanceEngine()
    state = {"stale_goals_count": 2}
    proposals = engine.evaluate_triggers(current_state=state, now=1000.0)
    prop = next(p for p in proposals if p.trigger_id == "trig_stale_goals")

    rejected = engine.reject_proposal(prop.proposal_id, reason="Not now")
    assert rejected.status == ProposalStatus.REJECTED
    assert rejected.user_decision == "rejected"
    assert rejected.decision_reason == "Not now"
