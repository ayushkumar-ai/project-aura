"""
Declarative benchmark scenario models and standard suites for Project AURA (Milestone 23).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class BenchmarkCategory(str, Enum):
    """Categories of benchmark scenarios."""

    STANDARD_WORKFLOW = "standard_workflow"
    AUTONOMOUS_AGENT = "autonomous_agent"
    HIERARCHICAL_GOAL = "hierarchical_goal"
    MULTI_AGENT_TEAM = "multi_agent_team"
    MEMORY_LIFECYCLE = "memory_lifecycle"
    SECURITY_ADVERSARIAL = "security_adversarial"
    RESILIENCE_RECOVERY = "resilience_recovery"


@dataclass(frozen=True)
class BenchmarkScenario:
    """Declarative specification for a single benchmark evaluation test case."""

    scenario_id: str
    name: str
    category: BenchmarkCategory
    description: str = ""
    prompt: str = ""
    expected_criteria: tuple[str, ...] = ()
    expected_grade: str = "A"
    timeout_seconds: float = 30.0
    runner_func: Callable[..., Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "prompt": self.prompt,
            "expected_criteria": list(self.expected_criteria),
            "expected_grade": self.expected_grade,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }
