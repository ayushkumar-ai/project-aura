"""Meta-Policy Strategy Selection Engine (M16).

Determines the optimal execution strategy for goals and tasks by analyzing goal criteria,
failure modes, strategy history, and empirically calibrated heuristics.
Strictly non-authorizing: selects strategy modalities without granting permissions.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.config import settings
from core.goal import Goal
from core.heuristic_calibrator import HeuristicCalibrator
from core.lifecycle_types import RuleStatus
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import (
    StrategyAttempt,
    StrategyType,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.meta_policy")


@dataclass(frozen=True)
class MetaPolicyDecision:
    """Outcome of MetaPolicy strategy evaluation."""

    selected_strategy: StrategyType
    rationale: str
    confidence: float = 1.0
    excluded_strategies: tuple[StrategyType, ...] = field(default_factory=tuple)
    applicable_heuristics: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.selected_strategy, str):
            object.__setattr__(self, "selected_strategy", StrategyType(self.selected_strategy))
        elif not isinstance(self.selected_strategy, StrategyType):
            raise TypeError("selected_strategy must be a StrategyType instance.")

        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))

        norm_ex = []
        for s in self.excluded_strategies:
            st = StrategyType(s) if isinstance(s, str) else s
            if not isinstance(st, StrategyType):
                raise TypeError("excluded_strategies items must be StrategyType.")
            if st not in norm_ex:
                norm_ex.append(st)
        object.__setattr__(self, "excluded_strategies", tuple(norm_ex))

        object.__setattr__(self, "applicable_heuristics", tuple(str(h).strip() for h in self.applicable_heuristics if str(h).strip()))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_strategy": self.selected_strategy.value,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "excluded_strategies": [s.value for s in self.excluded_strategies],
            "applicable_heuristics": list(self.applicable_heuristics),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetaPolicyDecision:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            selected_strategy=StrategyType(data.get("selected_strategy", StrategyType.DIRECT_SKILL.value)),
            rationale=str(data.get("rationale", "")),
            confidence=float(data.get("confidence", 1.0)),
            excluded_strategies=tuple(StrategyType(s) for s in data.get("excluded_strategies", [])),
            applicable_heuristics=tuple(data.get("applicable_heuristics", ())),
            metadata=dict(data.get("metadata", {})),
        )


class MetaPolicyEngine:
    """Selects execution strategy modalities for goals based on empirical evidence and lineage."""

    def __init__(
        self,
        lineage_store: StrategyLineageStore | None = None,
        calibrator: HeuristicCalibrator | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.lineage_store = lineage_store if lineage_store is not None else StrategyLineageStore()
        self.calibrator = calibrator
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "aura_strategy_selection_timeout_seconds", 5.0)
        )

    def select_strategy(
        self,
        goal: Goal,
        failure_category: str | None = None,
        has_new_observation_evidence: bool = False,
        context: dict[str, Any] | None = None,
    ) -> MetaPolicyDecision:
        """Evaluate goal state, lineage, failure modes, and calibrated heuristics to select a strategy."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        start_time = time.time()
        goal_id = goal.goal_id

        # 1. Determine excluded strategies from lineage store
        excluded = set()
        for st in StrategyType:
            if self.lineage_store.is_strategy_excluded(
                goal_id=goal_id,
                strategy_type=st,
                has_new_observation_evidence=has_new_observation_evidence,
            ):
                excluded.add(st)

        # 2. Extract applicable PROMOTED heuristics from calibrator (strictly exclude DEPRECATED)
        applicable_rules: list[str] = []
        heuristic_preferred_strategy: StrategyType | None = None

        if self.calibrator is not None:
            promoted_rules = self.calibrator.list_promoted_rules()
            goal_text = f"{goal.title} {goal.description}".lower()

            for rule in promoted_rules:
                trigger = rule.trigger_condition.lower()
                if trigger in goal_text or any(word in goal_text for word in trigger.split() if len(word) > 3):
                    applicable_rules.append(rule.rule_id)
                    # Check if heuristic recommends a specific strategy mode
                    rule_meta = rule.metadata
                    rec_strat = rule_meta.get("recommended_strategy")
                    if rec_strat and rec_strat in StrategyType._value2member_map_:
                        cand = StrategyType(rec_strat)
                        if cand not in excluded:
                            heuristic_preferred_strategy = cand

        # 3. Decision Logic
        last_attempt = self.lineage_store.get_last_attempt(goal_id)
        selected_strat: StrategyType
        rationale: str
        confidence: float = 0.85

        # Check timeout guard
        if (time.time() - start_time) > self.timeout_seconds:
            # Fallback on timeout
            selected_strat = StrategyType.DIRECT_SKILL
            rationale = "Strategy selection timed out; defaulting to DIRECT_SKILL."
            return MetaPolicyDecision(
                selected_strategy=selected_strat,
                rationale=rationale,
                confidence=0.50,
                excluded_strategies=tuple(excluded),
            )

        # If heuristic strongly recommends an unexcluded strategy
        if heuristic_preferred_strategy is not None and heuristic_preferred_strategy not in excluded:
            selected_strat = heuristic_preferred_strategy
            rationale = f"Selected {selected_strat.value} based on promoted empirical heuristic rules."
            confidence = 0.95

        elif last_attempt is None:
            # Initial evaluation (no prior attempts)
            # Complex goals (many criteria or subgoals) -> DECOMPOSED_HIERARCHICAL
            if len(goal.success_criteria) > 2 or bool(goal.subgoal_ids):
                candidate = StrategyType.DECOMPOSED_HIERARCHICAL
            elif any(k in f"{goal.title} {goal.description}".lower() for k in ("research", "find", "search", "investigate", "compare")):
                candidate = StrategyType.RESEARCH_ASSISTED_SYNTHESIS
            else:
                candidate = StrategyType.DIRECT_SKILL

            selected_strat = candidate if candidate not in excluded else StrategyType.DECOMPOSED_HIERARCHICAL
            rationale = f"Initial strategy selection for goal: {selected_strat.value}."

        else:
            # Failure recovery mode
            effective_failure = failure_category or last_attempt.failure_category
            if effective_failure:
                eff_lower = effective_failure.lower()
                if "timeout" in eff_lower:
                    # Timeout: break down into smaller steps or try fallback tool
                    candidates = [StrategyType.FALLBACK_TOOL_ROUTING, StrategyType.DECOMPOSED_HIERARCHICAL]
                elif "policy" in eff_lower or "permission" in eff_lower:
                    # Policy denial: interactive clarification or fallback
                    candidates = [StrategyType.HUMAN_INTERACTIVE_CLARIFICATION, StrategyType.FALLBACK_TOOL_ROUTING]
                elif "payload" in eff_lower or "tool_error" in eff_lower:
                    # Tool payload error: fallback tool or research synthesis
                    candidates = [StrategyType.FALLBACK_TOOL_ROUTING, StrategyType.RESEARCH_ASSISTED_SYNTHESIS]
                elif "dependency" in eff_lower:
                    # Missing prerequisite: decomposed hierarchical
                    candidates = [StrategyType.DECOMPOSED_HIERARCHICAL, StrategyType.RESEARCH_ASSISTED_SYNTHESIS]
                else:
                    candidates = [
                        StrategyType.DECOMPOSED_HIERARCHICAL,
                        StrategyType.RESEARCH_ASSISTED_SYNTHESIS,
                        StrategyType.FALLBACK_TOOL_ROUTING,
                        StrategyType.HUMAN_INTERACTIVE_CLARIFICATION,
                    ]
            else:
                candidates = [
                    StrategyType.DECOMPOSED_HIERARCHICAL,
                    StrategyType.RESEARCH_ASSISTED_SYNTHESIS,
                    StrategyType.FALLBACK_TOOL_ROUTING,
                    StrategyType.HUMAN_INTERACTIVE_CLARIFICATION,
                    StrategyType.DIRECT_SKILL,
                ]

            # Pick the first unexcluded candidate
            chosen = None
            for cand in candidates:
                if cand not in excluded:
                    chosen = cand
                    break

            if chosen is None:
                # All candidates excluded; fallback to interactive clarification or decomposed
                if StrategyType.HUMAN_INTERACTIVE_CLARIFICATION not in excluded:
                    chosen = StrategyType.HUMAN_INTERACTIVE_CLARIFICATION
                else:
                    # Forced reset on exhausted strategy choices
                    chosen = StrategyType.DECOMPOSED_HIERARCHICAL

            selected_strat = chosen
            rationale = f"Failure recovery pivot to {selected_strat.value} due to previous {effective_failure or 'failure'}."
            confidence = 0.80

        return MetaPolicyDecision(
            selected_strategy=selected_strat,
            rationale=rationale,
            confidence=confidence,
            excluded_strategies=tuple(sorted(excluded, key=lambda x: x.value)),
            applicable_heuristics=tuple(applicable_rules),
            metadata={"evaluated_at": time.time()},
        )
