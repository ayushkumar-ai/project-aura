import pytest

from core.consensus_engine import (
    AgentVote,
    ConsensusEngine,
    ConsensusResult,
    ConsensusStrategy,
)


def test_agent_vote_validation_and_bounds():
    vote = AgentVote(
        voter_role_id="Reviewer",
        decision="approve",
        confidence_score=1.5,  # Should clamp to 1.0
        weight=-2.0,  # Should clamp to 0.0
        rationale="Looks good",
    )

    assert vote.voter_role_id == "reviewer"
    assert vote.decision == "approve"
    assert vote.confidence_score == 1.0
    assert vote.weight == 0.0
    assert vote.rationale == "Looks good"


def test_unanimous_consensus_evaluation():
    engine = ConsensusEngine()

    votes_agree = [
        AgentVote(voter_role_id="reviewer", decision="approve", confidence_score=0.9),
        AgentVote(voter_role_id="security_auditor", decision="approve", confidence_score=0.95),
    ]
    res_agree = engine.evaluate_consensus(votes_agree, strategy=ConsensusStrategy.UNANIMOUS)
    assert res_agree.reached is True
    assert res_agree.selected_decision == "approve"
    assert res_agree.agreeing_votes == 2
    assert res_agree.disagreeing_votes == 0

    votes_disagree = [
        AgentVote(voter_role_id="reviewer", decision="approve", confidence_score=0.9),
        AgentVote(voter_role_id="security_auditor", decision="reject", confidence_score=0.95, rationale="SSRF found"),
    ]
    res_disagree = engine.evaluate_consensus(votes_disagree, strategy=ConsensusStrategy.UNANIMOUS)
    assert res_disagree.reached is False
    assert res_disagree.selected_decision is None
    assert len(res_disagree.dissenting_rationales) == 1
    assert "SSRF found" in res_disagree.dissenting_rationales[0]


def test_majority_vote_consensus():
    engine = ConsensusEngine()

    votes = [
        AgentVote(voter_role_id="architect", decision="design_a", confidence_score=0.8),
        AgentVote(voter_role_id="coder", decision="design_a", confidence_score=0.9),
        AgentVote(voter_role_id="reviewer", decision="design_b", confidence_score=0.7, rationale="Simpler"),
    ]

    res = engine.evaluate_consensus(votes, strategy=ConsensusStrategy.MAJORITY_VOTE, threshold=0.5)
    assert res.reached is True
    assert res.selected_decision == "design_a"
    assert res.agreeing_votes == 2
    assert res.disagreeing_votes == 1
    assert "Simpler" in res.dissenting_rationales[0]


def test_weighted_score_consensus():
    engine = ConsensusEngine()

    votes = [
        AgentVote(voter_role_id="coder", decision="option_fast", confidence_score=0.9, weight=1.0),
        AgentVote(voter_role_id="architect", decision="option_robust", confidence_score=0.9, weight=3.0),
    ]

    res = engine.evaluate_consensus(votes, strategy=ConsensusStrategy.WEIGHTED_SCORE, threshold=0.5)
    assert res.reached is True
    assert res.selected_decision == "option_robust"


def test_lead_decision_consensus():
    engine = ConsensusEngine()

    votes = [
        AgentVote(voter_role_id="coder", decision="option_fast"),
        AgentVote(voter_role_id="coordinator", decision="option_safe", confidence_score=0.95),
    ]

    res = engine.evaluate_consensus(
        votes,
        strategy=ConsensusStrategy.LEAD_DECISION,
        lead_role_id="coordinator",
    )
    assert res.reached is True
    assert res.selected_decision == "option_safe"
    assert res.agreeing_votes == 1


def test_synthesize_artifacts():
    engine = ConsensusEngine()

    artifacts = [
        {"role_id": "architect", "summary": "Interface design", "content": "class Foo:\n    pass"},
        {"role_id": "security_auditor", "summary": "Audit report", "content": "No CVEs found."},
    ]

    synthesis = engine.synthesize_artifacts(artifacts, lead_role_id="coordinator")
    assert "Synthesis coordinated by Lead [coordinator]" in synthesis
    assert "### [ARCHITECT] Deliverable - Interface design" in synthesis
    assert "### [SECURITY_AUDITOR] Deliverable - Audit report" in synthesis
