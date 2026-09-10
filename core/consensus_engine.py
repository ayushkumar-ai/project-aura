import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("aura.consensus_engine")


class ConsensusStrategy(str, Enum):
    """Strategies for aggregating multi-agent evaluations, votes, and decisions."""

    MAJORITY_VOTE = "majority_vote"
    UNANIMOUS = "unanimous"
    WEIGHTED_SCORE = "weighted_score"
    LEAD_DECISION = "lead_decision"


@dataclass
class AgentVote:
    """Represents an individual agent role's evaluation, score, and decision."""

    voter_role_id: str
    decision: Any
    confidence_score: float = 1.0
    weight: float = 1.0
    rationale: str = ""
    is_untrusted: bool = True
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.voter_role_id, str) or not self.voter_role_id.strip():
            raise ValueError("voter_role_id must be a non-empty string.")
        self.voter_role_id = self.voter_role_id.strip().lower()
        self.confidence_score = max(0.0, min(1.0, float(self.confidence_score)))
        self.weight = max(0.0, float(self.weight))
        if not isinstance(self.rationale, str):
            self.rationale = str(self.rationale)


@dataclass
class ConsensusResult:
    """Aggregated outcome of multi-agent voting or peer evaluation."""

    strategy: ConsensusStrategy
    reached: bool
    selected_decision: Any
    confidence: float = 0.0
    total_votes: int = 0
    agreeing_votes: int = 0
    disagreeing_votes: int = 0
    dissenting_rationales: list[str] = field(default_factory=list)
    synthesis_summary: str = ""
    is_untrusted: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert consensus result to serializable dictionary."""
        return {
            "strategy": self.strategy.value,
            "reached": self.reached,
            "selected_decision": self.selected_decision,
            "confidence": round(self.confidence, 4),
            "total_votes": self.total_votes,
            "agreeing_votes": self.agreeing_votes,
            "disagreeing_votes": self.disagreeing_votes,
            "dissenting_rationales": list(self.dissenting_rationales),
            "synthesis_summary": self.synthesis_summary,
            "is_untrusted": self.is_untrusted,
            "metadata": dict(self.metadata),
        }


class ConsensusEngine:
    """Deterministic aggregation engine for multi-agent voting, peer debates, and artifact synthesis."""

    def evaluate_consensus(
        self,
        votes: list[AgentVote],
        strategy: ConsensusStrategy | str = ConsensusStrategy.MAJORITY_VOTE,
        threshold: float = 0.5,
        lead_role_id: str | None = None,
    ) -> ConsensusResult:
        """Evaluate a list of votes under the specified ConsensusStrategy."""
        if not votes:
            raise ValueError("Cannot evaluate consensus on an empty list of votes.")

        strat = ConsensusStrategy(strategy) if isinstance(strategy, str) else strategy
        total_votes = len(votes)
        any_untrusted = any(v.is_untrusted for v in votes)

        if strat == ConsensusStrategy.UNANIMOUS:
            first_decision = votes[0].decision
            all_agree = all(v.decision == first_decision for v in votes)
            agreeing = total_votes if all_agree else sum(1 for v in votes if v.decision == first_decision)
            dissenting = [f"[{v.voter_role_id}]: {v.rationale}" for v in votes if v.decision != first_decision and v.rationale]
            avg_conf = sum(v.confidence_score for v in votes) / total_votes

            return ConsensusResult(
                strategy=strat,
                reached=all_agree,
                selected_decision=first_decision if all_agree else None,
                confidence=avg_conf if all_agree else 0.0,
                total_votes=total_votes,
                agreeing_votes=agreeing if all_agree else 0,
                disagreeing_votes=total_votes - agreeing if not all_agree else 0,
                dissenting_rationales=dissenting,
                synthesis_summary="Unanimous consensus achieved." if all_agree else "Failed unanimous consensus.",
                is_untrusted=any_untrusted,
            )

        elif strat == ConsensusStrategy.MAJORITY_VOTE:
            counts: dict[Any, int] = defaultdict(int)
            confidences: dict[Any, list[float]] = defaultdict(list)
            for v in votes:
                counts[v.decision] += 1
                confidences[v.decision].append(v.confidence_score)

            sorted_decisions = sorted(counts.items(), key=lambda item: item[1], reverse=True)
            top_decision, top_count = sorted_decisions[0]
            ratio = top_count / total_votes
            reached = ratio > threshold
            avg_conf = sum(confidences[top_decision]) / len(confidences[top_decision])
            dissenting = [f"[{v.voter_role_id}]: {v.rationale}" for v in votes if v.decision != top_decision and v.rationale]

            return ConsensusResult(
                strategy=strat,
                reached=reached,
                selected_decision=top_decision if reached else None,
                confidence=avg_conf * ratio,
                total_votes=total_votes,
                agreeing_votes=top_count,
                disagreeing_votes=total_votes - top_count,
                dissenting_rationales=dissenting,
                synthesis_summary=(
                    f"Majority consensus reached on '{top_decision}' ({top_count}/{total_votes} votes)."
                    if reached
                    else f"No majority reached (top vote ratio {ratio:.2f} <= {threshold:.2f})."
                ),
                is_untrusted=any_untrusted,
            )

        elif strat == ConsensusStrategy.WEIGHTED_SCORE:
            scores: dict[Any, float] = defaultdict(float)
            total_weight = sum(v.weight for v in votes) or float(total_votes)
            for v in votes:
                scores[v.decision] += v.confidence_score * v.weight

            sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            top_decision, top_score = sorted_scores[0]
            norm_score = top_score / total_weight if total_weight > 0 else 0.0
            reached = norm_score >= threshold
            agreeing = sum(1 for v in votes if v.decision == top_decision)
            dissenting = [f"[{v.voter_role_id}]: {v.rationale}" for v in votes if v.decision != top_decision and v.rationale]

            return ConsensusResult(
                strategy=strat,
                reached=reached,
                selected_decision=top_decision if reached else None,
                confidence=norm_score,
                total_votes=total_votes,
                agreeing_votes=agreeing,
                disagreeing_votes=total_votes - agreeing,
                dissenting_rationales=dissenting,
                synthesis_summary=(
                    f"Weighted consensus selected '{top_decision}' with score {norm_score:.2f} (threshold {threshold:.2f})."
                    if reached
                    else f"Weighted consensus below threshold ({norm_score:.2f} < {threshold:.2f})."
                ),
                is_untrusted=any_untrusted,
            )

        elif strat == ConsensusStrategy.LEAD_DECISION:
            norm_lead = lead_role_id.strip().lower() if lead_role_id else votes[0].voter_role_id
            lead_vote = next((v for v in votes if v.voter_role_id == norm_lead), votes[0])
            lead_decision = lead_vote.decision
            agreeing = sum(1 for v in votes if v.decision == lead_decision)
            dissenting = [f"[{v.voter_role_id}]: {v.rationale}" for v in votes if v.decision != lead_decision and v.rationale]

            return ConsensusResult(
                strategy=strat,
                reached=True,
                selected_decision=lead_decision,
                confidence=lead_vote.confidence_score,
                total_votes=total_votes,
                agreeing_votes=agreeing,
                disagreeing_votes=total_votes - agreeing,
                dissenting_rationales=dissenting,
                synthesis_summary=f"Lead agent [{norm_lead}] decided '{lead_decision}' ({agreeing}/{total_votes} peers agreed).",
                is_untrusted=any_untrusted,
            )

        else:
            raise ValueError(f"Unsupported ConsensusStrategy: {strat}")

    def synthesize_artifacts(
        self,
        artifacts: list[dict[str, Any]],
        lead_role_id: str | None = None,
    ) -> str:
        """Synthesize multiple agent deliverable sections into a coherent summary report."""
        if not artifacts:
            return "No artifacts submitted for synthesis."

        sections: list[str] = []
        for i, art in enumerate(artifacts, 1):
            role = art.get("role_id", f"Agent_{i}")
            content = art.get("content", "").strip()
            summary = art.get("summary", "")
            if content:
                header = f"### [{role.upper()}] Deliverable"
                if summary:
                    header += f" - {summary}"
                sections.append(f"{header}\n{content}")

        lead_prefix = f"Synthesis coordinated by Lead [{lead_role_id}]:\n\n" if lead_role_id else ""
        return lead_prefix + "\n\n".join(sections)
