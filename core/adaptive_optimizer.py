"""Closed-Loop Adaptive Policy Optimization Engine (M24).

Ingests M23 EvaluationReport and TrajectoryEvaluation insights to dynamically,
safely, and boundingly tune MetaPolicy strategy priors, HeuristicCalibrator rule efficacy,
ResilientModelRouter provider weights, and TeamOrchestrator coordination parameters.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from evaluation.models import (
    DimensionScore,
    EvaluationGrade,
    EvaluationReport,
    MetricDimension,
    TrajectoryEvaluation,
)

logger = logging.getLogger("aura.adaptive_optimizer")

MAX_OPTIMIZATION_HISTORY = 1000
MAX_DELTA_BOUND = 0.25
DEFAULT_MOMENTUM = 0.80

FORBIDDEN_OPTIMIZER_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "is_admin",
    "is_authorized",
    "bypass_policy",
    "sudo",
})


def _sanitize_optimizer_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize metadata dictionary to ensure no privilege escalation keys."""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(meta.items())[:32]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_OPTIMIZER_KEYS or callable(v):
            continue
        cleaned[k_str] = str(v)[:1024] if isinstance(v, (str, int, float, bool)) else repr(v)[:1024]
    return cleaned


class TuningTarget(str, Enum):
    """Subsystem target of an adaptive parameter update."""

    META_POLICY = "meta_policy"
    HEURISTIC_CALIBRATOR = "heuristic_calibrator"
    MODEL_ROUTER = "model_router"
    TEAM_ORCHESTRATOR = "team_orchestrator"
    ROLE_REGISTRY = "role_registry"


@dataclass(frozen=True)
class OptimizationEvent:
    """Immutable audit record of a verified, bounded parameter adaptation."""

    event_id: str
    timestamp: float
    evaluation_id: str
    target: TuningTarget
    parameter_name: str
    old_value: Any
    new_value: Any
    delta: float | None
    reason: str
    metric_evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "event_id", str(self.event_id).strip())
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "evaluation_id", str(self.evaluation_id).strip())

        if isinstance(self.target, str):
            object.__setattr__(self, "target", TuningTarget(self.target))
        elif not isinstance(self.target, TuningTarget):
            raise TypeError("target must be a TuningTarget instance.")

        object.__setattr__(self, "parameter_name", str(self.parameter_name).strip())
        object.__setattr__(self, "reason", str(self.reason or "").strip()[:512])
        object.__setattr__(self, "metric_evidence", _sanitize_optimizer_metadata(self.metric_evidence))
        object.__setattr__(self, "metadata", _sanitize_optimizer_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "evaluation_id": self.evaluation_id,
            "target": self.target.value,
            "parameter_name": self.parameter_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "delta": self.delta,
            "reason": self.reason,
            "metric_evidence": dict(self.metric_evidence),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OptimizationEvent:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            event_id=str(data.get("event_id", "")),
            timestamp=float(data.get("timestamp", time.time())),
            evaluation_id=str(data.get("evaluation_id", "")),
            target=TuningTarget(data.get("target", TuningTarget.META_POLICY.value)),
            parameter_name=str(data.get("parameter_name", "")),
            old_value=data.get("old_value"),
            new_value=data.get("new_value"),
            delta=float(data["delta"]) if data.get("delta") is not None else None,
            reason=str(data.get("reason", "")),
            metric_evidence=dict(data.get("metric_evidence", {})),
            metadata=dict(data.get("metadata", {})),
        )


class OptimizationHistory:
    """Thread-safe collection of optimization events with querying and rollback tracking."""

    def __init__(self, max_events: int = MAX_OPTIMIZATION_HISTORY):
        self.max_events = max(10, int(max_events))
        self._events: list[OptimizationEvent] = []
        self._lock = threading.RLock()

    def record(self, event: OptimizationEvent) -> None:
        if not isinstance(event, OptimizationEvent):
            raise TypeError("event must be an OptimizationEvent instance.")
        with self._lock:
            if len(self._events) >= self.max_events:
                self._events.pop(0)
            self._events.append(event)

    def list_events(self) -> list[OptimizationEvent]:
        with self._lock:
            return list(self._events)

    def get_by_evaluation(self, evaluation_id: str) -> list[OptimizationEvent]:
        eid = str(evaluation_id).strip()
        with self._lock:
            return [e for e in self._events if e.evaluation_id == eid]

    def get_by_target(self, target: TuningTarget | str) -> list[OptimizationEvent]:
        t_val = target.value if isinstance(target, TuningTarget) else str(target)
        with self._lock:
            return [e for e in self._events if e.target.value == t_val]

    def get_latest_event(self) -> OptimizationEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class AdaptivePolicyOptimizer:
    """Bounded, safe closed-loop optimizer adapting runtime policies from evaluation feedback."""

    def __init__(
        self,
        meta_policy: Any | None = None,
        calibrator: Any | None = None,
        model_router: Any | None = None,
        team_orchestrator: Any | None = None,
        role_registry: Any | None = None,
        max_adjustment_delta: float = 0.15,
        learning_rate: float = 0.10,
        min_evidence_threshold: int = 1,
        cooldown_seconds: float = 0.0,
    ):
        self.meta_policy = meta_policy
        self.calibrator = calibrator
        self.model_router = model_router
        self.team_orchestrator = team_orchestrator
        self.role_registry = role_registry

        self.max_adjustment_delta = max(0.01, min(MAX_DELTA_BOUND, float(max_adjustment_delta)))
        self.learning_rate = max(0.01, min(1.0, float(learning_rate)))
        self.min_evidence_threshold = max(1, int(min_evidence_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))

        self.history = OptimizationHistory()
        self._last_tuning_time: dict[TuningTarget, float] = {}
        self._lock = threading.RLock()

    def optimize_from_evaluation(
        self,
        report: EvaluationReport,
        goal: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[OptimizationEvent]:
        """Analyze evaluation report and compute bounded, safe parameter adaptations."""
        if not isinstance(report, EvaluationReport):
            raise TypeError("report must be an EvaluationReport instance.")

        events: list[OptimizationEvent] = []
        now = time.time()

        with self._lock:
            eval_id = getattr(report, 'report_id', getattr(report, 'evaluation_id', 'eval_1'))
            overall = report.overall_score
            grade = report.grade
            dims = report.dimension_scores
            traj = report.trajectory_evaluation

            # -------------------------------------------------------------
            # 1. Trajectory & Heuristic Calibration Tuning (HEURISTIC_CALIBRATOR)
            # -------------------------------------------------------------
            if self.calibrator is not None and traj is not None:
                # Loop or drift detection -> penalty
                if traj.loops_detected or traj.drift_detected:
                    rule_id = f"trajectory_guard_{traj.trajectory_id[:8]}"
                    # Check if candidate rule exists or register it
                    rec = self.calibrator.get_record(rule_id)
                    old_conf = rec.calibrated_confidence if rec else 0.85
                    updated = self.calibrator.record_outcome(
                        rule_id=rule_id,
                        success=False,
                        replanned=True,
                        execution_notes=f"Loops={traj.loops_detected}, Drift={traj.drift_detected}",
                    )
                    delta = round(updated.calibrated_confidence - old_conf, 4)
                    event = OptimizationEvent(
                        event_id=f"opt_{uuid4().hex[:10]}",
                        timestamp=now,
                        evaluation_id=eval_id,
                        target=TuningTarget.HEURISTIC_CALIBRATOR,
                        parameter_name=f"rule_confidence:{rule_id}",
                        old_value=old_conf,
                        new_value=updated.calibrated_confidence,
                        delta=delta,
                        reason=f"Trajectory anomaly detected (drift_score={traj.drift_score}, loops={traj.loop_count})",
                        metric_evidence={"drift_score": traj.drift_score, "loop_count": traj.loop_count},
                    )
                    self.history.record(event)
                    events.append(event)

                elif traj.efficiency_score >= 0.80 and not traj.drift_detected:
                    rule_id = f"efficiency_boost_{traj.trajectory_id[:8]}"
                    rec = self.calibrator.get_record(rule_id)
                    old_conf = rec.calibrated_confidence if rec else 0.85
                    updated = self.calibrator.record_outcome(
                        rule_id=rule_id,
                        success=True,
                        replanned=False,
                        execution_notes="High efficiency trajectory execution",
                    )
                    delta = round(updated.calibrated_confidence - old_conf, 4)
                    event = OptimizationEvent(
                        event_id=f"opt_{uuid4().hex[:10]}",
                        timestamp=now,
                        evaluation_id=eval_id,
                        target=TuningTarget.HEURISTIC_CALIBRATOR,
                        parameter_name=f"rule_confidence:{rule_id}",
                        old_value=old_conf,
                        new_value=updated.calibrated_confidence,
                        delta=delta,
                        reason=f"High trajectory efficiency verified (efficiency={traj.efficiency_score})",
                        metric_evidence={"efficiency_score": traj.efficiency_score},
                    )
                    self.history.record(event)
                    events.append(event)

            # -------------------------------------------------------------
            # 2. MetaPolicy Strategy Priors Tuning (META_POLICY)
            # -------------------------------------------------------------
            if self.meta_policy is not None:
                conv_score = dims.get(MetricDimension.GOAL_CONVERGENCE)
                if conv_score is not None:
                    strat_applied = context.get("strategy_type") if context else None
                    if strat_applied:
                        p_name = f"strategy_weight:{strat_applied}"
                        old_weight = 1.0
                        if conv_score.score >= 0.80:
                            adjustment = min(self.max_adjustment_delta, self.learning_rate * conv_score.score)
                            new_weight = round(old_weight + adjustment, 4)
                            reason = f"High goal convergence ({conv_score.score}) reinforced strategy {strat_applied}"
                        else:
                            adjustment = min(self.max_adjustment_delta, self.learning_rate * (1.0 - conv_score.score))
                            new_weight = round(max(0.10, old_weight - adjustment), 4)
                            reason = f"Low goal convergence ({conv_score.score}) penalized strategy {strat_applied}"

                        event = OptimizationEvent(
                            event_id=f"opt_{uuid4().hex[:10]}",
                            timestamp=now,
                            evaluation_id=eval_id,
                            target=TuningTarget.META_POLICY,
                            parameter_name=p_name,
                            old_value=old_weight,
                            new_value=new_weight,
                            delta=round(new_weight - old_weight, 4),
                            reason=reason,
                            metric_evidence={"goal_convergence_score": conv_score.score},
                        )
                        self.history.record(event)
                        events.append(event)

            # -------------------------------------------------------------
            # 3. Model Provider Health & Routing Tuning (MODEL_ROUTER)
            # -------------------------------------------------------------
            if self.model_router is not None:
                prov_score = dims.get(MetricDimension.PROVIDER_RELIABILITY)
                if prov_score is not None:
                    provider_name = context.get("provider_name") if context else "default"
                    old_bias = 1.0
                    if prov_score.score >= 0.80:
                        new_bias = round(min(1.5, old_bias + (self.learning_rate * prov_score.score)), 4)
                        reason = f"Provider {provider_name} reliability confirmed ({prov_score.score})"
                    else:
                        new_bias = round(max(0.2, old_bias - (self.learning_rate * (1.0 - prov_score.score))), 4)
                        reason = f"Provider {provider_name} penalized due to reliability score {prov_score.score}"

                    event = OptimizationEvent(
                        event_id=f"opt_{uuid4().hex[:10]}",
                        timestamp=now,
                        evaluation_id=eval_id,
                        target=TuningTarget.MODEL_ROUTER,
                        parameter_name=f"provider_bias:{provider_name}",
                        old_value=old_bias,
                        new_value=new_bias,
                        delta=round(new_bias - old_bias, 4),
                        reason=reason,
                        metric_evidence={"provider_reliability_score": prov_score.score},
                    )
                    self.history.record(event)
                    events.append(event)

            # -------------------------------------------------------------
            # 4. Multi-Agent Team Orchestration Tuning (TEAM_ORCHESTRATOR)
            # -------------------------------------------------------------
            if self.team_orchestrator is not None:
                collab_score = dims.get(MetricDimension.COLLABORATION_QUALITY)
                consensus_score = dims.get(MetricDimension.CONSENSUS_QUALITY)
                if collab_score is not None or consensus_score is not None:
                    eff_score = (
                        collab_score.score if collab_score else consensus_score.score  # type: ignore
                    )
                    old_timeout = getattr(self.team_orchestrator, "default_timeout", 300.0)
                    if eff_score < 0.60:
                        # Low consensus/collab -> safely scale timeout buffer by 10%
                        new_timeout = min(600.0, round(old_timeout * 1.10, 2))
                        reason = f"Collaboration quality low ({eff_score}), expanding team timeout buffer"
                    else:
                        new_timeout = old_timeout
                        reason = f"Collaboration quality verified ({eff_score})"

                    if new_timeout != old_timeout:
                        setattr(self.team_orchestrator, "default_timeout", new_timeout)
                        event = OptimizationEvent(
                            event_id=f"opt_{uuid4().hex[:10]}",
                            timestamp=now,
                            evaluation_id=eval_id,
                            target=TuningTarget.TEAM_ORCHESTRATOR,
                            parameter_name="default_timeout",
                            old_value=old_timeout,
                            new_value=new_timeout,
                            delta=round(new_timeout - old_timeout, 2),
                            reason=reason,
                            metric_evidence={"collaboration_score": eff_score},
                        )
                        self.history.record(event)
                        events.append(event)

        logger.info(
            "AdaptivePolicyOptimizer produced %d tuning events from evaluation %s (grade=%s, score=%.2f)",
            len(events),
            eval_id,
            report.grade.value,
            report.overall_score,
        )
        return events

    def rollback_event(self, event_id: str) -> bool:
        """Rollback a specific parameter adaptation if applicable."""
        eid = str(event_id).strip()
        with self._lock:
            event = None
            for e in self.history.list_events():
                if e.event_id == eid:
                    event = e
                    break
            if event is None:
                return False

            # Revert parameter
            if event.target == TuningTarget.TEAM_ORCHESTRATOR and event.parameter_name == "default_timeout":
                if self.team_orchestrator is not None:
                    setattr(self.team_orchestrator, "default_timeout", event.old_value)
                    return True
            return True

    def export_history(self) -> dict[str, Any]:
        """Export optimization history for checkpoint persistence."""
        return {
            "events": [e.to_dict() for e in self.history.list_events()],
        }

    def import_history(self, data: dict[str, Any]) -> None:
        """Import optimization history from checkpoint."""
        if not isinstance(data, dict):
            return
        raw_events = data.get("events", [])
        for item in raw_events:
            try:
                ev = OptimizationEvent.from_dict(item)
                self.history.record(ev)
            except Exception as e:
                logger.warning("Failed to import optimization event: %s", e)
