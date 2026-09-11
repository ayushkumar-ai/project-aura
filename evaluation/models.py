from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    """Result produced when evaluating an AURA execution (M6 Backward Compatibility)."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    details: dict[str, str] = Field(default_factory=dict)


class MetricDimension(str, Enum):
    """Core dimensions evaluated across AURA autonomous and multi-agent workloads."""

    GOAL_CONVERGENCE = "goal_convergence"
    TASK_COMPLETION = "task_completion"
    TRAJECTORY_QUALITY = "trajectory_quality"
    COLLABORATION_QUALITY = "collaboration_quality"
    DELEGATION_QUALITY = "delegation_quality"
    CONSENSUS_QUALITY = "consensus_quality"
    MEMORY_FIDELITY = "memory_fidelity"
    SAFETY_COMPLIANCE = "safety_compliance"
    PROVENANCE_INTEGRITY = "provenance_integrity"
    PROVIDER_RELIABILITY = "provider_reliability"
    RESILIENCE_EFFICIENCY = "resilience_efficiency"


class EvaluationGrade(str, Enum):
    """Deterministic evaluation grading classification."""

    A_EXCELLENT = "A"  # score >= 0.90
    B_GOOD = "B"       # score >= 0.80
    C_ACCEPTABLE = "C" # score >= 0.70
    D_MARGINAL = "D"   # score >= 0.60
    F_FAILED = "F"     # score < 0.60

    @classmethod
    def from_score(cls, score: float) -> EvaluationGrade:
        """Derive deterministic letter grade from normalized score [0.0, 1.0]."""
        bounded_score = max(0.0, min(1.0, float(score)))
        if bounded_score >= 0.90:
            return cls.A_EXCELLENT
        elif bounded_score >= 0.80:
            return cls.B_GOOD
        elif bounded_score >= 0.70:
            return cls.C_ACCEPTABLE
        elif bounded_score >= 0.60:
            return cls.D_MARGINAL
        else:
            return cls.F_FAILED


@dataclass(frozen=True)
class DimensionScore:
    """Normalized evaluation score and evidence for a specific evaluation dimension."""

    dimension: MetricDimension
    score: float
    passed: bool
    weight: float = 1.0
    metrics: dict[str, Any] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.dimension, MetricDimension):
            try:
                object.__setattr__(self, "dimension", MetricDimension(self.dimension))
            except Exception:
                raise TypeError(f"Invalid dimension type: {type(self.dimension)}")
        bounded_score = max(0.0, min(1.0, float(self.score)))
        object.__setattr__(self, "score", round(bounded_score, 4))
        object.__setattr__(self, "weight", max(0.0, float(self.weight)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "score": self.score,
            "passed": self.passed,
            "weight": self.weight,
            "metrics": dict(self.metrics),
            "details": dict(self.details),
            "diagnostics": list(self.diagnostics),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class TrajectoryEvaluation:
    """Detailed structural and semantic evaluation of an execution trajectory."""

    trajectory_id: str
    is_valid: bool
    drift_detected: bool = False
    drift_score: float = 0.0  # 0.0 (no drift) to 1.0 (complete drift)
    loops_detected: bool = False
    loop_count: int = 0
    completion_consistent: bool = True
    completion_finding: str = "Consistent"
    efficiency_score: float = 1.0  # 0.0 to 1.0
    step_count: int = 0
    tool_call_count: int = 0
    message_count: int = 0
    delegation_depth: int = 0
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "is_valid": self.is_valid,
            "drift_detected": self.drift_detected,
            "drift_score": round(self.drift_score, 4),
            "loops_detected": self.loops_detected,
            "loop_count": self.loop_count,
            "completion_consistent": self.completion_consistent,
            "completion_finding": self.completion_finding,
            "efficiency_score": round(self.efficiency_score, 4),
            "step_count": self.step_count,
            "tool_call_count": self.tool_call_count,
            "message_count": self.message_count,
            "delegation_depth": self.delegation_depth,
            "evidence": list(self.evidence),
            "confidence": round(self.confidence, 4),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class EvaluationReport:
    """Comprehensive evaluation scorecard aggregating multiple dimensions."""

    report_id: str
    target_id: str
    target_type: str
    overall_score: float
    passed: bool
    grade: EvaluationGrade
    dimension_scores: dict[MetricDimension, DimensionScore]
    trajectory_evaluation: TrajectoryEvaluation | None = None
    findings: tuple[str, ...] = ()
    safety_findings: tuple[str, ...] = ()
    trajectory_findings: tuple[str, ...] = ()
    provenance_findings: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "overall_score": round(self.overall_score, 4),
            "passed": self.passed,
            "grade": self.grade.value,
            "dimension_scores": {
                dim.value: score.to_dict()
                for dim, score in self.dimension_scores.items()
            },
            "trajectory_evaluation": (
                self.trajectory_evaluation.to_dict() if self.trajectory_evaluation else None
            ),
            "findings": list(self.findings),
            "safety_findings": list(self.safety_findings),
            "trajectory_findings": list(self.trajectory_findings),
            "provenance_findings": list(self.provenance_findings),
            "diagnostics": list(self.diagnostics),
            "recommendations": list(self.recommendations),
            "metadata": dict(self.metadata),
            "evaluated_at": self.evaluated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


@dataclass(frozen=True)
class BenchmarkRunSummary:
    """Aggregated benchmark execution summary and comparison scorecard."""

    benchmark_id: str
    suite_name: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    pass_rate: float
    mean_score: float
    mean_latency_seconds: float
    scenario_reports: dict[str, EvaluationReport]
    grade: EvaluationGrade
    started_at: float
    completed_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "suite_name": self.suite_name,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4),
            "mean_latency_seconds": round(self.mean_latency_seconds, 4),
            "scenario_reports": {
                sc_id: report.to_dict()
                for sc_id, report in self.scenario_reports.items()
            },
            "grade": self.grade.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)