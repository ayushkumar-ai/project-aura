"""M59 — Verification, Reflection, and Memory Learning Unit Tests."""

import pytest
from core.agent_mesh.learning import MemoryLearningBridge
from core.agent_mesh.reflection import BoundedReflectionEngine
from core.agent_mesh.types import (
    ActionType,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
    PlanStep,
    VerificationStatus,
)
from core.agent_mesh.verifier import ResultVerifier
from core.cognitive_memory.types import CognitiveMemoryType, ProvenanceType
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository


class TestVerificationAndReflectionUnit:
    def test_result_verifier_success_and_failure(self):
        # Invariant M59-F37
        verifier = ResultVerifier()

        # Calculation success
        calc_step = PlanStep(step_number=1, action_type=ActionType.TOOL, action_name="calculate")
        st, detail = verifier.verify_step_outcome(calc_step, {"result": {"result": 42}})
        assert st == VerificationStatus.VERIFIED_SUCCESS

        # Calculation failure
        st_f, detail_f = verifier.verify_step_outcome(calc_step, {"result": {"error": "Division by zero"}})
        assert st_f == VerificationStatus.VERIFIED_FAILURE

        # Device write verification
        write_step = PlanStep(step_number=2, action_type=ActionType.DEVICE, action_name="write_sandboxed_file")
        st_w, _ = verifier.verify_step_outcome(write_step, {"result": {"status": "written", "bytes_written": 128}})
        assert st_w == VerificationStatus.VERIFIED_SUCCESS

        # Empty result failure
        st_empty, _ = verifier.verify_step_outcome(write_step, {})
        assert st_empty == VerificationStatus.VERIFIED_FAILURE

    def test_bounded_reflection_decisions(self):
        # Invariants M59-F38, M59-F39
        reflector = BoundedReflectionEngine()
        step = PlanStep(step_number=1, action_type=ActionType.TOOL, action_name="fetch_api")
        budget = AgentRunBudget(max_retries=2)

        # 1. Transient failure with retries left -> retry allowed
        dec = reflector.evaluate_failure(
            step=step,
            verification_status=VerificationStatus.VERIFIED_FAILURE,
            verification_detail="Socket timeout on fetch",
            current_retry_count=0,
            budget=budget,
        )
        assert dec.should_retry is True
        assert dec.abort_execution is False

        # 2. Max retries exceeded -> abort
        dec_abort = reflector.evaluate_failure(
            step=step,
            verification_status=VerificationStatus.VERIFIED_FAILURE,
            verification_detail="Socket timeout on fetch",
            current_retry_count=2,
            budget=budget,
        )
        assert dec_abort.should_retry is False
        assert dec_abort.abort_execution is True

        # 3. Security / Policy error -> retry strictly prohibited (No privilege escalation)
        dec_sec = reflector.evaluate_failure(
            step=step,
            verification_status=VerificationStatus.VERIFIED_FAILURE,
            verification_detail="Policy denied execution of step",
            current_retry_count=0,
            budget=budget,
        )
        assert dec_sec.should_retry is False
        assert dec_sec.abort_execution is True

    def test_memory_learning_bridge_provenance(self):
        # Invariants M59-F40, M59-F41
        mem_repo = InMemoryCognitiveMemoryRepository()
        bridge = MemoryLearningBridge(memory_repo=mem_repo)

        run = AgentRun(
            run_id="run_learning_test",
            tenant_id="tenant_1",
            intent="Process data pipeline",
            status=AgentRunStatus.COMPLETED,
            iteration_count=3,
            tool_call_count=4,
        )

        # 1. Auto trace receives TOOL_OBSERVED provenance
        mem = bridge.record_run_experience(run)
        assert mem is not None
        assert mem.provenance_type == ProvenanceType.TOOL_OBSERVED
        assert mem.memory_type == CognitiveMemoryType.EPISODIC
        assert mem.confidence == 0.85

        # 2. Confirmed user feedback receives USER_EXPLICIT provenance
        mem_user = bridge.record_run_experience(run, explicit_user_feedback="Great execution")
        assert mem_user is not None
        assert mem_user.provenance_type == ProvenanceType.USER_EXPLICIT
        assert mem_user.confidence == 1.0
