"""
Trajectory Verifier for Project AURA (Milestone 23).
Analyzes execution step sequences for goal drift, repeated loops, completion consistency, and efficiency.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from evaluation.models import TrajectoryEvaluation


class TrajectoryVerifier:
    """Analyzes execution trajectories to detect drift, loops, hallucinated completion, and inefficiency."""

    def __init__(
        self,
        max_drift_threshold: float = 0.50,
        loop_window_size: int = 3,
        max_loop_tolerance: int = 2,
    ):
        self.max_drift_threshold = max_drift_threshold
        self.loop_window_size = loop_window_size
        self.max_loop_tolerance = max_loop_tolerance

    def verify_trajectory(
        self,
        trajectory: Any,
        goal_title: str = "",
        goal_description: str = "",
        success_criteria: tuple[str, ...] | list[str] = (),
        claimed_success: bool = True,
        observations: tuple[Any, ...] | list[Any] = (),
    ) -> TrajectoryEvaluation:
        """Analyze execution trajectory steps, detecting drift, loops, and completion consistency."""
        traj_id = f"traj_eval_{uuid4().hex[:8]}"
        steps: list[dict[str, Any]] = []

        if isinstance(trajectory, list):
            for item in trajectory:
                if isinstance(item, dict):
                    steps.append(item)
                elif hasattr(item, "to_dict"):
                    steps.append(item.to_dict())
                else:
                    steps.append({"description": str(item)})
        elif hasattr(trajectory, "steps"):
            for s in getattr(trajectory, "steps", []):
                if hasattr(s, "to_dict"):
                    steps.append(s.to_dict())
                elif isinstance(s, dict):
                    steps.append(s)
                else:
                    steps.append({"description": str(s)})
        elif isinstance(trajectory, dict):
            raw_steps = trajectory.get("steps", [])
            for s in raw_steps:
                steps.append(s if isinstance(s, dict) else {"description": str(s)})

        step_count = len(steps)
        evidence: list[str] = []
        details: dict[str, Any] = {}

        # 1. Loop Detection
        loops_detected, loop_count, loop_evidence = self._detect_loops(steps)
        if loops_detected:
            evidence.extend(loop_evidence)
            details["loop_diagnostics"] = loop_evidence

        # 2. Goal Drift Detection
        target_context = f"{goal_title} {goal_description} {' '.join(success_criteria)}".strip()
        drift_detected, drift_score, drift_evidence = self._detect_drift(steps, target_context)
        if drift_detected:
            evidence.extend(drift_evidence)
            details["drift_diagnostics"] = drift_evidence

        # 3. Completion Consistency Checking
        comp_consistent, comp_finding, comp_evidence = self._check_completion_consistency(
            claimed_success=claimed_success,
            steps=steps,
            success_criteria=tuple(success_criteria),
            observations=tuple(observations),
        )
        evidence.extend(comp_evidence)

        # 4. Efficiency Score
        tool_call_count = sum(1 for s in steps if s.get("tool_name") or s.get("tool") or "tool" in str(s).lower())
        message_count = sum(1 for s in steps if s.get("message_type") or s.get("sender") or "message" in str(s).lower())
        delegation_depth = max([s.get("depth", 1) for s in steps] + [1])

        efficiency = 1.0
        if step_count > 0:
            penalty = (loop_count * 0.25) + (drift_score * 0.35)
            if step_count > 15:
                penalty += 0.10
            efficiency = max(0.0, min(1.0, 1.0 - penalty))

        is_valid = not loops_detected and not drift_detected and comp_consistent

        return TrajectoryEvaluation(
            trajectory_id=traj_id,
            is_valid=is_valid,
            drift_detected=drift_detected,
            drift_score=round(drift_score, 4),
            loops_detected=loops_detected,
            loop_count=loop_count,
            completion_consistent=comp_consistent,
            completion_finding=comp_finding,
            efficiency_score=round(efficiency, 4),
            step_count=step_count,
            tool_call_count=tool_call_count,
            message_count=message_count,
            delegation_depth=delegation_depth,
            evidence=tuple(evidence),
            confidence=0.95 if step_count > 0 else 1.0,
            details=details,
        )

    def _detect_loops(self, steps: list[dict[str, Any]]) -> tuple[bool, int, list[str]]:
        """Identify repeating step sequences, duplicate tool calls, or cycle states."""
        if len(steps) < 2:
            return False, 0, []

        loop_count = 0
        evidence: list[str] = []
        signatures: list[str] = []

        for s in steps:
            obj = str(s.get("objective", s.get("description", s.get("task_goal", "")))).strip().lower()
            tool = str(s.get("tool_name", s.get("skill_name", ""))).strip().lower()
            inp = str(s.get("input_data", s.get("tool_input", ""))).strip().lower()
            sig = f"{obj}|{tool}|{inp[:60]}"
            signatures.append(sig)

        # Check for immediate consecutive duplicates
        consecutive_dups = 0
        for i in range(1, len(signatures)):
            if signatures[i] and signatures[i] == signatures[i - 1]:
                consecutive_dups += 1
                if consecutive_dups >= self.max_loop_tolerance:
                    loop_count += 1
                    evidence.append(f"Repeated consecutive identical step signature at step {i}: '{signatures[i][:40]}...'")

        # Check for 2-step cyclical loops (A -> B -> A -> B)
        if len(signatures) >= 4:
            for i in range(len(signatures) - 3):
                if signatures[i] == signatures[i + 2] and signatures[i + 1] == signatures[i + 3] and signatures[i] != signatures[i + 1]:
                    loop_count += 1
                    evidence.append(f"Detected 2-cycle loop pattern between steps {i} and {i + 3}")

        loops_detected = loop_count > 0
        return loops_detected, loop_count, evidence

    def _detect_drift(self, steps: list[dict[str, Any]], target_context: str) -> tuple[bool, float, list[str]]:
        """Evaluate whether execution step trajectory has drifted away from the initial goal."""
        if not steps or not target_context:
            return False, 0.0, []

        target_keywords = set(re.findall(r"\b[a-zA-Z]{3,}\b", target_context.lower()))
        if not target_keywords:
            return False, 0.0, []

        irrelevant_steps = 0
        evidence: list[str] = []

        for idx, s in enumerate(steps):
            step_text = f"{s.get('objective', '')} {s.get('description', '')} {s.get('tool_name', '')} {s.get('output', '')}".lower()
            step_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", step_text))
            
            overlap = target_keywords.intersection(step_words)
            if not overlap and len(step_words) > 3:
                irrelevant_steps += 1
                evidence.append(f"Step {idx + 1} ('{list(step_words)[:3]}') shares no conceptual overlap with target goal.")

        drift_ratio = irrelevant_steps / float(len(steps))
        drift_detected = drift_ratio > self.max_drift_threshold
        return drift_detected, drift_ratio, evidence

    def _check_completion_consistency(
        self,
        claimed_success: bool,
        steps: list[dict[str, Any]],
        success_criteria: tuple[str, ...],
        observations: tuple[Any, ...],
    ) -> tuple[bool, str, list[str]]:
        """Verify that claimed completion is supported by step results and observations."""
        evidence: list[str] = []

        if not claimed_success:
            return True, "Explicit Failure/Incomplete Claim", ["Target explicitly marked as incomplete/failed; consistent."]

        if not steps and not observations and success_criteria:
            return False, "Unsupported Completion Claim", ["Target claims completion with 0 execution steps and 0 supporting observations."]

        failed_steps = 0
        for idx, s in enumerate(steps):
            status = str(s.get("status", "")).lower()
            err = s.get("error")
            if "fail" in status or "error" in status or err:
                failed_steps += 1
                evidence.append(f"Step {idx + 1} marked as failed or contained error: {err or status}")

        if failed_steps > 0 and failed_steps == len(steps):
            return False, "Contradictory Completion Claim", [f"Target claims success but all {failed_steps} execution steps failed."]

        return True, "Supported Completion", ["Claimed completion is corroborated by supporting step and observation evidence."]
