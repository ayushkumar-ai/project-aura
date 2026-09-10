import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.consensus_engine import ConsensusStrategy

logger = logging.getLogger("aura.team_types")


class TeamTopology(str, Enum):
    """Collaboration topologies for multi-agent teams."""

    HIERARCHICAL = "hierarchical"
    SEQUENTIAL_PIPELINE = "sequential_pipeline"
    ROUND_ROBIN_DEBATE = "round_robin_debate"
    CONSENSUS_VOTING = "consensus_voting"


@dataclass
class TeamMember:
    """Represents a member within a collaborative agent team."""

    role_id: str
    instance_id: str = field(default_factory=lambda: str(uuid4()))
    weight: float = 1.0
    is_lead: bool = False

    def __post_init__(self):
        if not isinstance(self.role_id, str) or not self.role_id.strip():
            raise ValueError("role_id must be a non-empty string.")
        self.role_id = self.role_id.strip().lower()
        self.weight = max(0.0, float(self.weight))


@dataclass
class TeamDefinition:
    """Configuration and composition of a multi-agent collaborative team."""

    team_id: str
    name: str
    description: str = ""
    members: list[TeamMember] = field(default_factory=list)
    topology: TeamTopology = TeamTopology.HIERARCHICAL
    consensus_strategy: ConsensusStrategy = ConsensusStrategy.MAJORITY_VOTE
    max_iterations: int = 5
    timeout_seconds: float = 300.0
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.team_id, str) or not self.team_id.strip():
            raise ValueError("team_id must be a non-empty string.")
        self.team_id = self.team_id.strip().lower()

        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string.")
        self.name = self.name.strip()

        if isinstance(self.topology, str):
            self.topology = TeamTopology(self.topology)
        elif not isinstance(self.topology, TeamTopology):
            raise TypeError("topology must be an instance of TeamTopology.")

        if isinstance(self.consensus_strategy, str):
            self.consensus_strategy = ConsensusStrategy(self.consensus_strategy)
        elif not isinstance(self.consensus_strategy, ConsensusStrategy):
            raise TypeError("consensus_strategy must be an instance of ConsensusStrategy.")

        if not isinstance(self.max_iterations, int) or self.max_iterations < 1:
            raise ValueError("max_iterations must be a positive integer.")

        self.timeout_seconds = max(1.0, float(self.timeout_seconds))

    def get_lead_member(self) -> TeamMember | None:
        """Find the designated lead team member, or first member if none marked."""
        for m in self.members:
            if m.is_lead:
                return m
        return self.members[0] if self.members else None

    def get_member_roles(self) -> list[str]:
        """List distinct role IDs in this team."""
        return list({m.role_id for m in self.members})


@dataclass
class TeamExecutionResult:
    """Aggregated output and telemetry from a multi-agent team collaboration."""

    team_id: str
    task: str
    topology: TeamTopology
    success: bool
    final_output: str
    subtask_results: dict[str, Any] = field(default_factory=dict)
    messages_exchanged: int = 0
    total_latency_seconds: float = 0.0
    iterations: int = 1
    consensus_score: float = 1.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert team execution result to a serializable dictionary."""
        return {
            "team_id": self.team_id,
            "task": self.task,
            "topology": self.topology.value,
            "success": self.success,
            "final_output": self.final_output,
            "subtask_results": dict(self.subtask_results),
            "messages_exchanged": self.messages_exchanged,
            "total_latency_seconds": round(self.total_latency_seconds, 4),
            "iterations": self.iterations,
            "consensus_score": round(self.consensus_score, 4),
            "error": self.error,
            "metadata": dict(self.metadata),
        }
