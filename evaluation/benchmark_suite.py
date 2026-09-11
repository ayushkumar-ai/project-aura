"""
Benchmark Suite for Project AURA (Milestone 23).
Orchestrates executing and evaluating collections of benchmark scenarios.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from evaluation.engine import EvaluationEngine
from evaluation.models import (
    BenchmarkRunSummary,
    EvaluationGrade,
    EvaluationReport,
)
from evaluation.scenarios import BenchmarkCategory, BenchmarkScenario

logger = logging.getLogger("aura.evaluation.benchmark_suite")


class BenchmarkSuite:
    """Executes collections of benchmark scenarios against AURA and generates scorecards."""

    def __init__(
        self,
        name: str = "AURA Standard Benchmark Suite",
        evaluation_engine: EvaluationEngine | None = None,
        default_timeout: float = 60.0,
    ):
        self.name = name
        self.evaluation_engine = evaluation_engine or EvaluationEngine()
        self.default_timeout = default_timeout
        self._scenarios: dict[str, BenchmarkScenario] = {}

    def register_scenario(self, scenario: BenchmarkScenario) -> None:
        """Register a benchmark scenario in the suite."""
        if not isinstance(scenario, BenchmarkScenario):
            raise TypeError("scenario must be an instance of BenchmarkScenario.")
        if scenario.scenario_id in self._scenarios:
            raise ValueError(f"Scenario with ID '{scenario.scenario_id}' is already registered.")
        self._scenarios[scenario.scenario_id] = scenario

    def get_scenario(self, scenario_id: str) -> BenchmarkScenario | None:
        """Retrieve registered scenario by ID."""
        return self._scenarios.get(scenario_id)

    def list_scenarios(self, category: BenchmarkCategory | None = None) -> list[BenchmarkScenario]:
        """List registered benchmark scenarios, optionally filtered by category."""
        if category is not None:
            return [s for s in self._scenarios.values() if s.category == category]
        return list(self._scenarios.values())

    def run(
        self,
        runtime_or_aura: Any,
        scenario_ids: list[str] | tuple[str, ...] | None = None,
        stop_on_failure: bool = False,
    ) -> BenchmarkRunSummary:
        """Execute selected or all registered scenarios and produce an aggregate scorecard."""
        bench_id = f"bench_run_{uuid4().hex[:8]}"
        started_at = time.time()
        
        target_scenarios = (
            [self._scenarios[sid] for sid in scenario_ids if sid in self._scenarios]
            if scenario_ids
            else list(self._scenarios.values())
        )

        total = len(target_scenarios)
        passed_count = 0
        failed_count = 0
        scores: list[float] = []
        latencies: list[float] = []
        scenario_reports: dict[str, EvaluationReport] = {}

        for sc in target_scenarios:
            sc_start = time.time()
            try:
                # 1. Execute scenario
                if sc.runner_func is not None:
                    exec_res = sc.runner_func(runtime_or_aura, sc)
                elif hasattr(runtime_or_aura, "run_task"):
                    exec_res = runtime_or_aura.run_task(sc.prompt)
                elif hasattr(runtime_or_aura, "execute"):
                    exec_res = runtime_or_aura.execute(sc.prompt)
                elif hasattr(runtime_or_aura, "run"):
                    exec_res = runtime_or_aura.run(sc.prompt)
                else:
                    raise RuntimeError("Provided runtime does not support standard execution methods.")

                sc_elapsed = time.time() - sc_start
                latencies.append(sc_elapsed)

                # 2. Evaluate execution outcome
                report = self.evaluation_engine.evaluate(
                    target=exec_res,
                    target_id=sc.scenario_id,
                    target_type=sc.category.value,
                    expected_criteria=sc.expected_criteria,
                    context={"scenario_name": sc.name, "category": sc.category.value},
                )
                scenario_reports[sc.scenario_id] = report
                scores.append(report.overall_score)

                if report.passed:
                    passed_count += 1
                else:
                    failed_count += 1
                    if stop_on_failure:
                        break

            except Exception as e:
                sc_elapsed = time.time() - sc_start
                latencies.append(sc_elapsed)
                failed_count += 1
                logger.error("Benchmark scenario '%s' encountered error: %s", sc.scenario_id, e)
                
                # Generate failing report
                err_report = self.evaluation_engine.evaluate(
                    target={"error": str(e), "success": False, "status": "failed"},
                    target_id=sc.scenario_id,
                    target_type=sc.category.value,
                    expected_criteria=sc.expected_criteria,
                    context={"error": str(e)},
                )
                scenario_reports[sc.scenario_id] = err_report
                scores.append(0.0)

                if stop_on_failure:
                    break

        completed_at = time.time()
        pass_rate = (passed_count / float(total)) if total > 0 else 0.0
        mean_score = (sum(scores) / float(len(scores))) if scores else 0.0
        mean_latency = (sum(latencies) / float(len(latencies))) if latencies else 0.0
        overall_grade = EvaluationGrade.from_score(mean_score)

        return BenchmarkRunSummary(
            benchmark_id=bench_id,
            suite_name=self.name,
            total_scenarios=total,
            passed_scenarios=passed_count,
            failed_scenarios=failed_count,
            pass_rate=round(pass_rate, 4),
            mean_score=round(mean_score, 4),
            mean_latency_seconds=round(mean_latency, 4),
            scenario_reports=scenario_reports,
            grade=overall_grade,
            started_at=started_at,
            completed_at=completed_at,
            metadata={"suite_name": self.name},
        )
