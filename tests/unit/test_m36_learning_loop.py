"""Unit tests for M36 Experience & Learning Loop Subsystem."""

from core.learning_loop_engine import ExperienceLearningEngine
from core.learning_loop_types import (
    InteractionOutcome,
    PatternConfidenceStatus,
)


def test_heuristic_creation_and_strengthening():
    engine = ExperienceLearningEngine()

    # First interaction - successful
    outcome1 = InteractionOutcome(
        interaction_id="int_1",
        task_pattern="deploy_docker_service",
        input_prompt="Deploy backend API to docker",
        tool_sequence=["docker_build", "docker_run"],
        success=True,
    )
    h1 = engine.record_interaction(outcome1)
    assert h1.status == PatternConfidenceStatus.CANDIDATE
    assert h1.support_count == 1
    assert h1.confidence > 0.5

    # Second interaction - successful
    outcome2 = InteractionOutcome(
        interaction_id="int_2",
        task_pattern="deploy_docker_service",
        input_prompt="Deploy frontend to docker",
        tool_sequence=["docker_build", "docker_run"],
        success=True,
    )
    h2 = engine.record_interaction(outcome2)
    assert h2.support_count == 2

    # Third interaction - successful with positive feedback
    outcome3 = InteractionOutcome(
        interaction_id="int_3",
        task_pattern="deploy_docker_service",
        input_prompt="Deploy worker to docker",
        tool_sequence=["docker_build", "docker_run"],
        success=True,
        user_feedback_score=1.0,
    )
    h3 = engine.record_interaction(outcome3)
    assert h3.support_count == 3
    assert h3.status == PatternConfidenceStatus.PROVEN
    assert h3.confidence >= 0.7


def test_heuristic_weakening_and_deprecation_on_failure():
    engine = ExperienceLearningEngine()

    # Repeated failures on a pattern
    for idx in range(3):
        outcome = InteractionOutcome(
            interaction_id=f"fail_{idx}",
            task_pattern="unsupported_legacy_script",
            input_prompt="Run perl script",
            success=False,
            error_details="Interpreter not found",
        )
        h = engine.record_interaction(outcome)

    assert h.failure_count == 3
    assert h.confidence < 0.3
    assert h.status == PatternConfidenceStatus.DEPRECATED


def test_learning_report_generation():
    engine = ExperienceLearningEngine()
    engine.record_interaction(InteractionOutcome(interaction_id="1", task_pattern="test_a", input_prompt="a", success=True))
    engine.record_interaction(InteractionOutcome(interaction_id="2", task_pattern="test_b", input_prompt="b", success=False))

    report = engine.generate_report()
    assert report.total_interactions == 2
    assert report.success_rate == 0.5
    assert report.total_heuristics == 2
