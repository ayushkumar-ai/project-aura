"""Empirical Heuristic Calibration and Efficacy Tracking (M15).

Tracks historical efficacy, success/failure outcomes, and replanning triggers
for distilled heuristics and reflection rules. Promotes validated heuristics
and deprecates failing heuristics based on empirical evidence.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from core.lifecycle_types import (
    RuleEfficacyRecord,
    RuleStatus,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.heuristic_calibrator")


class HeuristicCalibrator:
    """Tracks and calibrates the efficacy of distilled heuristic rules over time."""

    def __init__(
        self,
        min_trials_for_promotion: int = 3,
        deprecation_failure_rate: float = 0.60,
        records: dict[str, RuleEfficacyRecord] | None = None,
        persistence_path: Path | str | None = None,
    ) -> None:
        self.min_trials_for_promotion = max(1, int(min_trials_for_promotion))
        self.deprecation_failure_rate = max(0.0, min(1.0, float(deprecation_failure_rate)))
        self._records: dict[str, RuleEfficacyRecord] = dict(records) if records else {}
        self.persistence_path = Path(persistence_path) if persistence_path else None

        if self.persistence_path and self.persistence_path.exists() and not self._records:
            self.load_from_file()

    @property
    def records(self) -> dict[str, RuleEfficacyRecord]:
        return dict(self._records)

    def register_rule(
        self,
        rule_id: str,
        trigger_condition: str,
        base_confidence: float = 0.85,
        metadata: dict[str, Any] | None = None,
    ) -> RuleEfficacyRecord:
        """Register a heuristic rule for empirical tracking if not already present."""
        clean_id = str(rule_id).strip()
        if not clean_id:
            raise ValueError("rule_id must be a non-empty string.")

        if clean_id in self._records:
            return self._records[clean_id]

        record = RuleEfficacyRecord(
            rule_id=clean_id,
            trigger_condition=str(trigger_condition).strip(),
            status=RuleStatus.CANDIDATE,
            base_confidence=float(base_confidence),
            calibrated_confidence=float(base_confidence),
            efficacy_score=1.0,
            metadata=strip_forbidden_metadata_keys(metadata or {}),
        )
        self._records[clean_id] = record
        self._auto_save()
        return record

    def record_outcome(
        self,
        rule_id: str,
        success: bool,
        replanned: bool = False,
        execution_notes: str | None = None,
    ) -> RuleEfficacyRecord:
        """Record the outcome of applying a heuristic rule in a workflow."""
        clean_id = str(rule_id).strip()
        if not clean_id:
            raise ValueError("rule_id must be a non-empty string.")

        now = time.time()
        record = self._records.get(clean_id)
        if record is None:
            # Auto-register if not yet registered
            record = RuleEfficacyRecord(
                rule_id=clean_id,
                trigger_condition=f"heuristic_{clean_id}",
                status=RuleStatus.CANDIDATE,
                base_confidence=0.85,
                calibrated_confidence=0.85,
                efficacy_score=1.0,
            )

        new_trigger_count = record.trigger_count + 1
        new_success_count = record.success_count + (1 if success else 0)
        new_failure_count = record.failure_count + (0 if success else 1)
        new_replan_count = record.replan_count + (1 if replanned else 0)

        new_efficacy = new_success_count / max(1, new_trigger_count)
        # Calibrated confidence combines base confidence (40%) and empirical efficacy (60%)
        calibrated_conf = max(0.05, min(1.0, (0.40 * record.base_confidence) + (0.60 * new_efficacy)))

        failure_rate = new_failure_count / max(1, new_trigger_count)

        # Status transition evaluation
        if new_trigger_count >= self.min_trials_for_promotion and new_efficacy >= 0.80:
            new_status = RuleStatus.PROMOTED
        elif new_trigger_count >= self.min_trials_for_promotion and failure_rate >= self.deprecation_failure_rate:
            new_status = RuleStatus.DEPRECATED
        elif record.status == RuleStatus.CANDIDATE and new_trigger_count >= 1:
            new_status = RuleStatus.ACTIVE
        else:
            new_status = record.status

        updated_meta = dict(record.metadata)
        if execution_notes:
            history = updated_meta.get("notes_history", [])
            if isinstance(history, list):
                history.append({"time": now, "success": success, "note": str(execution_notes)})
                updated_meta["notes_history"] = history[-10:]  # keep last 10

        updated_record = RuleEfficacyRecord(
            rule_id=clean_id,
            trigger_condition=record.trigger_condition,
            status=new_status,
            trigger_count=new_trigger_count,
            success_count=new_success_count,
            failure_count=new_failure_count,
            replan_count=new_replan_count,
            base_confidence=record.base_confidence,
            calibrated_confidence=calibrated_conf,
            efficacy_score=new_efficacy,
            last_evaluated_at=now,
            metadata=strip_forbidden_metadata_keys(updated_meta),
        )

        self._records[clean_id] = updated_record
        self._auto_save()
        return updated_record

    def get_record(self, rule_id: str) -> RuleEfficacyRecord | None:
        """Get the efficacy record for a rule if present."""
        return self._records.get(str(rule_id).strip())

    def get_calibrated_confidence(self, rule_id: str, default: float = 0.85) -> float:
        """Get the current calibrated confidence for a rule."""
        rec = self._records.get(str(rule_id).strip())
        if rec is None:
            return float(default)
        return rec.calibrated_confidence

    def is_rule_usable(self, rule_id: str) -> bool:
        """Check if a rule is in an active/usable state (not deprecated)."""
        rec = self._records.get(str(rule_id).strip())
        if rec is None:
            return True
        return rec.status != RuleStatus.DEPRECATED

    def list_promoted_rules(self) -> list[RuleEfficacyRecord]:
        """Return all rules that have earned PROMOTED status."""
        return [r for r in self._records.values() if r.status == RuleStatus.PROMOTED]

    def list_deprecated_rules(self) -> list[RuleEfficacyRecord]:
        """Return all rules that have been DEPRECATED due to high failure rates."""
        return [r for r in self._records.values() if r.status == RuleStatus.DEPRECATED]

    def list_active_rules(self) -> list[RuleEfficacyRecord]:
        """Return all active and promoted rules."""
        return [r for r in self._records.values() if r.status in (RuleStatus.ACTIVE, RuleStatus.PROMOTED)]

    def to_dict(self) -> dict[str, Any]:
        """Serialize calibrator state to a JSON-serializable dictionary."""
        return {
            "min_trials_for_promotion": self.min_trials_for_promotion,
            "deprecation_failure_rate": self.deprecation_failure_rate,
            "records": {k: v.to_dict() for k, v in self._records.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], persistence_path: Path | str | None = None) -> HeuristicCalibrator:
        """Restore calibrator state from dictionary."""
        min_trials = int(data.get("min_trials_for_promotion", 3))
        deprecate_rate = float(data.get("deprecation_failure_rate", 0.60))
        raw_records = data.get("records", {})
        records = {k: RuleEfficacyRecord.from_dict(v) for k, v in raw_records.items()}
        return cls(
            min_trials_for_promotion=min_trials,
            deprecation_failure_rate=deprecate_rate,
            records=records,
            persistence_path=persistence_path,
        )

    def save_to_file(self, filepath: Path | str | None = None) -> None:
        """Persist state to JSON file."""
        target = Path(filepath) if filepath else self.persistence_path
        if target is None:
            raise ValueError("No persistence path specified.")

        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        temp_path.replace(target)

    def load_from_file(self, filepath: Path | str | None = None) -> None:
        """Load state from JSON file."""
        target = Path(filepath) if filepath else self.persistence_path
        if target is None or not target.exists():
            return

        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            restored = HeuristicCalibrator.from_dict(data)
            self._records = restored._records
            self.min_trials_for_promotion = restored.min_trials_for_promotion
            self.deprecation_failure_rate = restored.deprecation_failure_rate
        except Exception as e:
            logger.error("Failed to load calibrator state from %s: %s", target, e)

    def _auto_save(self) -> None:
        """Helper to save to file if persistence_path is set."""
        if self.persistence_path:
            try:
                self.save_to_file()
            except Exception as e:
                logger.warning("Auto-save failed: %s", e)
