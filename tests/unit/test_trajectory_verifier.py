"""
Unit tests for Milestone 23 TrajectoryVerifier.
Tests detection of goal drift, loop patterns, and completion consistency.
"""

import pytest
from evaluation.trajectory_verifier import TrajectoryVerifier


def test_trajectory_verifier_no_drift_and_valid():
    verifier = TrajectoryVerifier()
    steps = [
        {"objective": "Query database for user logs", "tool_name": "db_query", "status": "completed"},
        {"objective": "Parse user logs for security anomalies", "tool_name": "log_parser", "status": "completed"},
        {"objective": "Assemble security audit report", "tool_name": "report_builder", "status": "completed"},
    ]

    res = verifier.verify_trajectory(
        trajectory=steps,
        goal_title="User Log Security Audit",
        goal_description="Query and analyze logs for security anomalies",
        success_criteria=("Assemble security audit report",),
        claimed_success=True,
        observations=("Logs parsed successfully", "Report assembled"),
    )

    assert res.is_valid is True
    assert res.drift_detected is False
    assert res.loops_detected is False
    assert res.completion_consistent is True
    assert res.efficiency_score >= 0.85
    assert res.step_count == 3


def test_trajectory_verifier_detects_consecutive_loop():
    verifier = TrajectoryVerifier(max_loop_tolerance=2)
    # 3 identical consecutive step signatures
    steps = [
        {"objective": "Fetch external dataset", "tool_name": "http_fetch", "input_data": "https://api.test/data"},
        {"objective": "Fetch external dataset", "tool_name": "http_fetch", "input_data": "https://api.test/data"},
        {"objective": "Fetch external dataset", "tool_name": "http_fetch", "input_data": "https://api.test/data"},
    ]

    res = verifier.verify_trajectory(
        trajectory=steps,
        goal_title="Fetch External Dataset",
        claimed_success=True,
    )

    assert res.loops_detected is True
    assert res.loop_count >= 1
    assert res.is_valid is False
    assert res.efficiency_score < 1.0


def test_trajectory_verifier_detects_cyclical_loop():
    verifier = TrajectoryVerifier()
    # 2-cycle loop (A -> B -> A -> B)
    steps = [
        {"objective": "Step A: Check state", "tool_name": "state_tool", "input_data": "1"},
        {"objective": "Step B: Revert state", "tool_name": "revert_tool", "input_data": "2"},
        {"objective": "Step A: Check state", "tool_name": "state_tool", "input_data": "1"},
        {"objective": "Step B: Revert state", "tool_name": "revert_tool", "input_data": "2"},
    ]

    res = verifier.verify_trajectory(
        trajectory=steps,
        goal_title="Manage State",
        claimed_success=True,
    )

    assert res.loops_detected is True
    assert res.is_valid is False


def test_trajectory_verifier_detects_goal_drift():
    verifier = TrajectoryVerifier(max_drift_threshold=0.40)
    steps = [
        {"objective": "Play chess online against engine", "description": "Playing chess", "tool_name": "game_tool"},
        {"objective": "Order pizza delivery for dinner", "description": "Ordering food", "tool_name": "food_tool"},
        {"objective": "Check weather forecast in Tokyo", "description": "Weather lookup", "tool_name": "weather_tool"},
    ]

    res = verifier.verify_trajectory(
        trajectory=steps,
        goal_title="Compile Linux Kernel",
        goal_description="Build and install custom optimized Linux kernel modules",
        success_criteria=("Kernel modules built",),
        claimed_success=True,
    )

    assert res.drift_detected is True
    assert res.drift_score > 0.40
    assert res.is_valid is False


def test_trajectory_verifier_detects_unsupported_completion():
    verifier = TrajectoryVerifier()
    # 0 steps, 0 observations, but claimed success with criteria
    res = verifier.verify_trajectory(
        trajectory=[],
        goal_title="Deploy Mission Critical Service",
        success_criteria=("Service deployed to Kubernetes", "Health probes passing"),
        claimed_success=True,
        observations=(),
    )

    assert res.completion_consistent is False
    assert res.completion_finding == "Unsupported Completion Claim"
    assert res.is_valid is False


def test_trajectory_verifier_detects_contradictory_completion():
    verifier = TrajectoryVerifier()
    steps = [
        {"objective": "Compile source code", "status": "failed", "error": "SyntaxError on line 42"},
        {"objective": "Run unit tests", "status": "error", "error": "Build artifact missing"},
    ]

    res = verifier.verify_trajectory(
        trajectory=steps,
        goal_title="Build and Test Software",
        claimed_success=True,  # Contradictory claim
    )

    assert res.completion_consistent is False
    assert res.completion_finding == "Contradictory Completion Claim"
    assert res.is_valid is False
