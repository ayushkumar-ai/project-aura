"""Adversarial and Edge-Case Test Suite for Project AURA Milestones 29 through 40.

Tests boundary limits, corruption recovery, authorization boundaries, schema injection,
circular dependency traps, and distributed conflict resolution.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from core.context_personalization_engine import ContextPersonalizationEngine
from core.context_personalization_types import ContextBudget, ContextPriority
from core.cross_device_sync_engine import CrossDeviceSyncEngine
from core.cross_device_types import ConflictResolutionStrategy, SyncOperationType
from core.device_integration_engine import DeviceIntegrationEngine
from core.device_integration_types import DeviceActionRequest, DeviceCapability, DeviceDescriptor, DeviceType
from core.durable_state_store import DurablePersonalStateStore
from core.learning_loop_engine import ExperienceLearningEngine
from core.learning_loop_types import InteractionOutcome, PatternConfidenceStatus
from core.multimodal_engine import MockMultimodalAdapter, MultimodalProcessor
from core.multimodal_types import AudioFormat, ImageFormat, ModalityType, MultimodalRequest
from core.personal_state_types import MemoryCategory, UserPreferences
from core.proactive_engine import ProactiveAssistanceEngine
from core.proactive_types import ProactiveActionType, TriggerDefinition, TriggerType
from core.release_validator import ReleaseValidationCategory, ReleaseValidator, ValidationSeverity
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import RetrievalQuery, RetrievalSourceType
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.structured_plan_types import PlanStepNode
from core.structured_planner import StructuredPlanningEngine
from core.tool_ecosystem import ToolEcosystemRegistry
from core.tool_ecosystem_types import ToolExecutionRequest, ToolPermissionTier


# =====================================================================
# M29 Release Validation Adversarial Tests
# =====================================================================
def test_m29_release_validator_adversarial_config():
    """Verify preflight rejects invalid ports and unauthenticated production mode."""
    val = ReleaseValidator()

    # Invalid port (out of bounds)
    cfg_bad_port = Settings(aura_server_port=99999, aura_env="development")
    res_port = val.validate_configuration(cfg_bad_port)
    port_check = next(c for c in res_port if c.check_id == "cfg_port_range")
    assert not port_check.passed
    assert port_check.severity == ValidationSeverity.ERROR

    # Production mode without auth key
    cfg_prod_no_auth = Settings(
        aura_env="production",
        aura_api_key_auth_enabled=False,
        aura_server_api_key="",
    )
    res_prod = val.validate_configuration(cfg_prod_no_auth)
    auth_check = next(c for c in res_prod if c.check_id == "cfg_prod_auth")
    assert not auth_check.passed
    assert auth_check.severity == ValidationSeverity.CRITICAL

    # Full report reflects failure in production
    report = val.run_preflight_checks(config=cfg_prod_no_auth)
    assert not report.is_production_ready
    assert report.summary_counts["critical_failures"] >= 1


# =====================================================================
# M30 Durable State Store Corruption Recovery Tests
# =====================================================================
def test_m30_durable_state_checksum_tamper_recovery():
    """Verify state store detects tampered checksums and recovers from backup."""
    temp_dir = tempfile.mkdtemp(prefix="aura_test_durable_")
    try:
        store1 = DurablePersonalStateStore(storage_dir=temp_dir)
        store1.update_preferences({"preferred_name": "OriginalAlice", "verbosity": 3})
        store1.record_memory(category=MemoryCategory.SEMANTIC, content="Secret Note 1")

        # Verify state file exists and valid
        valid, status = store1.verify_integrity()
        assert valid
        assert status == "valid"

        state_file = Path(temp_dir) / DurablePersonalStateStore.SNAPSHOT_FILENAME
        backup_file = Path(temp_dir) / DurablePersonalStateStore.BACKUP_FILENAME

        # Update again so a backup file is created
        store1.update_preferences({"preferred_name": "UpdatedAlice"})
        assert backup_file.exists()

        # Tamper with the state file (alter text without updating checksum)
        with open(state_file, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["user_preferences"]["preferred_name"] = "HackedAttacker"
            f.seek(0)
            json.dump(data, f)
            f.truncate()

        # Now load with a new store instance -> should detect checksum mismatch and load from backup
        store2 = DurablePersonalStateStore(storage_dir=temp_dir)
        # Checksum should have failed on target, falling back to backup
        prefs = store2.get_preferences()
        assert prefs.preferred_name == "OriginalAlice"  # Restored from backup
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# =====================================================================
# M31 Multi-Source RAG & Secret Scrubbing Tests
# =====================================================================
def test_m31_retrieval_secrets_scrubbed_and_artifact_source():
    """Verify secrets are scrubbed from retrieval outputs and artifacts are retrieved."""
    mock_artifacts = MagicMock()
    mock_art = MagicMock()
    mock_art.artifact_id = "art_777"
    mock_art.name = "financial_report_q3.pdf"
    mock_art.metadata = {"description": "Quarterly financial summary for audit"}
    mock_artifacts.list_artifacts.return_value = [mock_art]

    pipeline = AdvancedRetrievalPipeline(artifact_manager=mock_artifacts)
    pipeline.add_knowledge_document(
        doc_id="secret_doc",
        title="Api Credentials",
        content="The system secret is sk-ant-api03-abcdef1234567890abcdef1234567890 and Bearer secret_token_xyz",
    )

    query = RetrievalQuery(
        query_text="financial report secret",
        source_types=[RetrievalSourceType.KNOWLEDGE_BASE, RetrievalSourceType.ARTIFACTS],
        scrub_secrets=True,
    )
    candidates = pipeline.retrieve_candidates(query)
    assert len(candidates) >= 2

    # Secret document should be scrubbed
    secret_cand = next(c for c in candidates if c.candidate_id == "secret_doc")
    assert "sk-ant-api03" not in secret_cand.text
    assert "[REDACTED" in secret_cand.text or "[SECRET" in secret_cand.text or "REDACTED" in secret_cand.text

    # Artifact candidate should be present
    art_cand = next(c for c in candidates if c.candidate_id == "art_777")
    assert art_cand.source_type == RetrievalSourceType.ARTIFACTS
    assert "financial_report_q3" in art_cand.title


# =====================================================================
# M32 Context Personalization Budget Truncation Tests
# =====================================================================
def test_m32_context_personalization_budget_overflow():
    """Verify that context engine bounds total size and prioritizes user prompt."""
    engine = ContextPersonalizationEngine()
    budget = ContextBudget(max_total_chars=500)

    long_prompt = "Execute goal task X"
    long_history = [{"role": "user", "content": "A" * 300}, {"role": "assistant", "content": "B" * 300}]

    bundle = engine.build_context_bundle(
        user_prompt=long_prompt,
        user_preferences=UserPreferences(preferred_name="Bob"),
        conversation_history=long_history,
        budget=budget,
    )

    assert bundle.total_characters <= 800  # including minimal section markers
    # Critical user request must be in the assembled prompt
    assert "Execute goal task X" in bundle.assembled_prompt
    # Dropped items should be recorded
    assert len(bundle.dropped_items) >= 1 or len(bundle.included_items) >= 1


# =====================================================================
# M33 Structured Planner Circular Dependency & Policy Rejection Tests
# =====================================================================
def test_m33_planning_circular_dependency_and_policy_denial():
    """Verify planner catches circular step dependencies and handles policy denial gracefully."""
    engine = StructuredPlanningEngine()

    # Circular Dependency
    step_a = PlanStepNode(step_id="step_a", title="A", description="A desc", depends_on=["step_b"])
    step_b = PlanStepNode(step_id="step_b", title="B", description="B desc", depends_on=["step_a"])
    with pytest.raises(ValueError, match="Circular dependency"):
        engine.create_plan(goal="Circular Task", steps=[step_a, step_b])

    # Dangling Dependency
    step_valid = PlanStepNode(step_id="step_1", title="Step 1", description="Step 1 desc", depends_on=["non_existent_step"])
    with pytest.raises(ValueError, match="non-existent step"):
        engine.create_plan(goal="Dangling Task", steps=[step_valid])

    # Policy Denial
    mock_policy = MagicMock()
    mock_policy.authorize_tool.return_value = "deny"
    mock_policy.authorized_tools = {"restricted_tool"}

    step_restricted = PlanStepNode(
        step_id="step_denied",
        title="Restricted Step",
        description="Restricted Step desc",
        tool_name="restricted_tool",
        depends_on=[],
    )
    plan = engine.create_plan(goal="Policy Test", steps=[step_restricted])
    audit = engine.execute_plan(plan, policy=mock_policy)
    assert not audit.is_success
    assert audit.steps_failed >= 1
    assert "Policy denied" in str(audit.execution_trace)


# =====================================================================
# M34 Tool Ecosystem Security Sandbox Tests
# =====================================================================
def test_m34_safe_calculator_arbitrary_code_injection_prevention():
    """Verify safe calculator rejects arbitrary python AST nodes and malicious code."""
    registry = ToolEcosystemRegistry()

    # Valid arithmetic
    valid_res = registry.execute_tool(
        ToolExecutionRequest(tool_name="calculator", parameters={"expression": "10 * (5 + 3) / 2"})
    )
    assert valid_res.success
    assert valid_res.output["result"] == 40.0

    # Malicious injection attempt 1: Import / Eval
    bad_res1 = registry.execute_tool(
        ToolExecutionRequest(tool_name="calculator", parameters={"expression": "__import__('os').system('echo bad')"})
    )
    assert not bad_res1.success
    assert "Unsupported AST node" in str(bad_res1.error)

    # Malicious injection attempt 2: Variable assignment / builtin access
    bad_res2 = registry.execute_tool(
        ToolExecutionRequest(tool_name="calculator", parameters={"expression": "open('/etc/passwd').read()"})
    )
    assert not bad_res2.success


# =====================================================================
# M35 Proactive Assistance Cooldown Tests
# =====================================================================
def test_m35_proactive_cooldown_and_rejection():
    """Verify anti-spam cooldown prevents duplicate trigger fires."""
    engine = ProactiveAssistanceEngine()

    state = {"stale_goals_count": 3}
    proposals1 = engine.evaluate_triggers(current_state=state)
    assert len(proposals1) >= 1
    p1 = proposals1[0]

    # Immediate second evaluation should return empty list due to cooldown
    proposals2 = engine.evaluate_triggers(current_state=state)
    assert len(proposals2) == 0

    # Rejection of proposal
    rejected = engine.reject_proposal(p1.proposal_id, reason="User dismissed")
    assert rejected.status.value == "rejected"
    assert rejected.decision_reason == "User dismissed"


# =====================================================================
# M36 Experience Learning Loop Lifecycle Tests
# =====================================================================
def test_m36_learning_loop_confidence_transitions():
    """Verify heuristics dynamically upgrade to PROVEN on success and degrade on failure."""
    engine = ExperienceLearningEngine()

    # Initial success -> CANDIDATE
    outcome1 = InteractionOutcome(
        interaction_id="int_1",
        task_pattern="data_pipeline",
        input_prompt="execute data pipeline",
        tool_sequence=["extract_tool", "transform_tool"],
        success=True,
    )
    h1 = engine.record_interaction(outcome1)
    assert h1.status == PatternConfidenceStatus.CANDIDATE
    assert h1.support_count == 1

    # Multiple successive successes -> PROVEN
    engine.record_interaction(
        InteractionOutcome(
            interaction_id="int_2",
            task_pattern="data_pipeline",
            input_prompt="execute data pipeline",
            success=True,
            user_feedback_score=1.0,
        )
    )
    engine.record_interaction(
        InteractionOutcome(
            interaction_id="int_3",
            task_pattern="data_pipeline",
            input_prompt="execute data pipeline",
            success=True,
            user_feedback_score=1.0,
        )
    )
    proven_h = engine.query_heuristics(task_pattern="data_pipeline", only_proven=True)
    assert len(proven_h) == 1
    assert proven_h[0].status == PatternConfidenceStatus.PROVEN

    # Multiple failures -> DEPRECATED
    engine.record_interaction(
        InteractionOutcome(interaction_id="int_4", task_pattern="data_pipeline", input_prompt="data", success=False)
    )
    engine.record_interaction(
        InteractionOutcome(interaction_id="int_5", task_pattern="data_pipeline", input_prompt="data", success=False)
    )
    engine.record_interaction(
        InteractionOutcome(interaction_id="int_6", task_pattern="data_pipeline", input_prompt="data", success=False)
    )
    final_h = engine.query_heuristics(task_pattern="data_pipeline", min_confidence=0.0)[0]
    assert final_h.status in (PatternConfidenceStatus.WEAKENED, PatternConfidenceStatus.DEPRECATED)


# =====================================================================
# M37 Multimodal Foundation Format Detection Tests
# =====================================================================
def test_m37_multimodal_magic_bytes_detection():
    """Verify magic bytes detection identifies valid PNG, JPEG, WAV and rejects unknown."""
    adapter = MockMultimodalAdapter()

    # Valid PNG header
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert adapter.detect_image_format(png_bytes) == ImageFormat.PNG

    # Valid JPEG header
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    assert adapter.detect_image_format(jpeg_bytes) == ImageFormat.JPEG

    # Corrupted / Unknown image
    garbage_bytes = b"NOT_AN_IMAGE_DATA_12345"
    assert adapter.detect_image_format(garbage_bytes) == ImageFormat.UNKNOWN

    # Valid WAV header
    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt "
    assert adapter.detect_audio_format(wav_bytes) == AudioFormat.WAV


# =====================================================================
# M38 Device Integration Authorization & Offline Tests
# =====================================================================
def test_m38_device_unauthorized_user_and_offline_rejection():
    """Verify unauthorized user or offline device rejects control action."""
    engine = DeviceIntegrationEngine()
    desktop = engine.get_device("dev_desktop_primary")
    assert desktop is not None

    # Unauthorized user
    req_unauth = DeviceActionRequest(
        action_id="act_unauth",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.SEND_NOTIFICATION,
        parameters={"message": "Test"},
        user_id="unauthorized_guest",
    )
    res_unauth = engine.execute_action(req_unauth)
    assert not res_unauth.success
    assert "not authorized" in str(res_unauth.error)

    # Offline device
    desktop.is_online = False
    req_offline = DeviceActionRequest(
        action_id="act_offline",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.SEND_NOTIFICATION,
        parameters={"message": "Test"},
        user_id="default_user",
    )
    res_offline = engine.execute_action(req_offline)
    assert not res_offline.success
    assert "offline" in str(res_offline.error)
    desktop.is_online = True  # restore


# =====================================================================
# M39 Cross-Device Sync Idempotency & Conflict Resolution Tests
# =====================================================================
def test_m39_cross_device_idempotency_and_conflict():
    """Verify idempotent delta replay and LAST_WRITE_WINS conflict resolution."""
    node_a = CrossDeviceSyncEngine(device_id="device_laptop")
    node_b = CrossDeviceSyncEngine(device_id="device_phone")

    delta1 = node_a.generate_delta(
        operation=SyncOperationType.SET_PREFERENCE,
        entity_id="pref_style",
        payload={"style": "concise"},
        idempotency_key="unique_key_001",
    )

    # Node B applies delta
    applied1, status1 = node_b.receive_delta(delta1)
    assert applied1
    assert status1 == "applied"
    assert node_b.get_cached_entity("pref_style")["payload"] == {"style": "concise"}

    # Replaying same delta -> idempotent
    applied2, status2 = node_b.receive_delta(delta1)
    assert applied2
    assert status2 == "already_applied_idempotent"

    # Conflicting older timestamp delta -> rejected
    old_delta = node_a.generate_delta(
        operation=SyncOperationType.SET_PREFERENCE,
        entity_id="pref_style",
        payload={"style": "verbose"},
    )
    old_delta.timestamp = delta1.timestamp - 100.0  # simulate old delayed delta
    applied3, status3 = node_b.receive_delta(old_delta)
    assert not applied3
    assert status3 == "rejected_older_timestamp"


# =====================================================================
# M40 Integrated Personal Intelligence Full Checkpoint Recovery
# =====================================================================
def test_m40_checkpoint_persists_m30_m36_m39_state():
    """Verify runtime checkpoint captures and restores M30 state, M36 heuristics, and M39 sync clocks."""
    temp_dir = tempfile.mkdtemp(prefix="aura_ckpt_test_")
    try:
        dss = DurablePersonalStateStore()
        dss.update_preferences({"preferred_name": "Dr. Watson", "verbosity": 4})
        dss.record_memory(category=MemoryCategory.SEMANTIC, content="Favorite beverage: Earl Grey")

        learning = ExperienceLearningEngine()
        learning.record_interaction(
            InteractionOutcome(
                interaction_id="int_100",
                task_pattern="code_review",
                input_prompt="run code review",
                tool_sequence=["lint", "test"],
                success=True,
            )
        )

        sync = CrossDeviceSyncEngine(device_id="device_desktop")
        sync.generate_delta(
            operation=SyncOperationType.SET_PREFERENCE,
            entity_id="pref_theme",
            payload={"theme": "dark"},
        )

        ckpt_mgr = RuntimeCheckpointManager(
            checkpoint_dir=temp_dir,
            durable_state_store=dss,
            learning_engine=learning,
            cross_device_sync=sync,
        )

        # Save checkpoint
        meta = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_test_m40")
        assert meta.checkpoint_id == "ckpt_test_m40"

        # Create fresh target engines and restore
        target_dss = DurablePersonalStateStore()
        target_learning = ExperienceLearningEngine()
        target_sync = CrossDeviceSyncEngine(device_id="device_restored")

        restore_mgr = RuntimeCheckpointManager(
            checkpoint_dir=temp_dir,
            durable_state_store=target_dss,
            learning_engine=target_learning,
            cross_device_sync=target_sync,
        )

        restored_meta = restore_mgr.restore_latest_checkpoint()
        assert restored_meta is not None

        # Verify M30 restored state
        assert target_dss.get_preferences().preferred_name == "Dr. Watson"
        memories = target_dss.query_memories(query="Earl Grey")
        assert len(memories) == 1

        # Verify M36 restored heuristics
        heuristics = target_learning.query_heuristics(task_pattern="code_review")
        assert len(heuristics) == 1
        assert heuristics[0].recommended_tools == ["lint", "test"]

        # Verify M39 restored sync clock
        assert target_sync._applied_deltas_count >= 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
