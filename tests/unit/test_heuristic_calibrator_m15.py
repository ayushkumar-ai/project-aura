"""
Unit tests for Milestone 15 core/heuristic_calibrator.py.
Tests empirical rule efficacy calibration, status promotions/deprecations,
calibrated confidence calculations, and persistence.
"""

import pytest
import time
import tempfile
from pathlib import Path
from core.lifecycle_types import (
    RuleEfficacyRecord,
    RuleStatus,
)
from core.heuristic_calibrator import HeuristicCalibrator


class TestHeuristicCalibrator:
    def test_register_rule(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=3, deprecation_failure_rate=0.60)
        record = calibrator.register_rule(
            rule_id="rule_001",
            trigger_condition="use_subprocess_for_cli",
            base_confidence=0.80,
            metadata={"domain": "system"},
        )
        assert record.rule_id == "rule_001"
        assert record.status == RuleStatus.CANDIDATE
        assert record.base_confidence == 0.80
        assert record.trigger_count == 0
        assert record.efficacy_score == 1.0
        assert calibrator.is_rule_usable("rule_001") is True

    def test_rule_promotion_workflow(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=3, deprecation_failure_rate=0.60)
        calibrator.register_rule("rule_opt", "optimize_query", base_confidence=0.85)

        # Trial 1: success -> ACTIVE
        rec1 = calibrator.record_outcome("rule_opt", success=True)
        assert rec1.trigger_count == 1
        assert rec1.success_count == 1
        assert rec1.status == RuleStatus.ACTIVE
        assert rec1.efficacy_score == 1.0

        # Trial 2: success -> ACTIVE
        rec2 = calibrator.record_outcome("rule_opt", success=True)
        assert rec2.trigger_count == 2
        assert rec2.status == RuleStatus.ACTIVE

        # Trial 3: success -> PROMOTED
        rec3 = calibrator.record_outcome("rule_opt", success=True)
        assert rec3.trigger_count == 3
        assert rec3.success_count == 3
        assert rec3.status == RuleStatus.PROMOTED
        assert rec3.calibrated_confidence > 0.90
        assert calibrator.is_rule_usable("rule_opt") is True
        assert len(calibrator.list_promoted_rules()) == 1

    def test_rule_deprecation_workflow(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=3, deprecation_failure_rate=0.60)
        calibrator.register_rule("rule_broken", "fragile_heuristic", base_confidence=0.75)

        # 3 failures in a row
        calibrator.record_outcome("rule_broken", success=False, replanned=True)
        calibrator.record_outcome("rule_broken", success=False)
        rec_final = calibrator.record_outcome("rule_broken", success=False)

        assert rec_final.trigger_count == 3
        assert rec_final.failure_count == 3
        assert rec_final.replan_count == 1
        assert rec_final.efficacy_score == 0.0
        assert rec_final.status == RuleStatus.DEPRECATED
        assert calibrator.is_rule_usable("rule_broken") is False
        assert len(calibrator.list_deprecated_rules()) == 1

    def test_calibrated_confidence_calculation(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=4)
        calibrator.register_rule("rule_mixed", "mixed_results", base_confidence=0.80)

        # 2 successes, 2 failures -> efficacy = 0.50
        calibrator.record_outcome("rule_mixed", success=True)
        calibrator.record_outcome("rule_mixed", success=True)
        calibrator.record_outcome("rule_mixed", success=False)
        rec = calibrator.record_outcome("rule_mixed", success=False)

        assert rec.efficacy_score == 0.50
        # calibrated = 0.40 * 0.80 + 0.60 * 0.50 = 0.32 + 0.30 = 0.62
        assert abs(rec.calibrated_confidence - 0.62) < 0.01

    def test_persistence_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "calibrator_state.json"
            calibrator1 = HeuristicCalibrator(persistence_path=file_path)
            calibrator1.register_rule("rule_persist", "persist_test", base_confidence=0.90)
            calibrator1.record_outcome("rule_persist", success=True)
            calibrator1.record_outcome("rule_persist", success=True)
            calibrator1.record_outcome("rule_persist", success=True)
            calibrator1.save_to_file()

            assert file_path.exists()

            calibrator2 = HeuristicCalibrator(persistence_path=file_path)
            calibrator2.load_from_file()
            rec = calibrator2.get_record("rule_persist")
            assert rec is not None
            assert rec.status == RuleStatus.PROMOTED
            assert rec.success_count == 3
