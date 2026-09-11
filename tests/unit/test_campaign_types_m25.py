"""Unit tests for Milestone 25 Campaign Contracts & Schemas."""

import pytest
from core.artifact_types import ArtifactType
from core.campaign_types import (
    ArtifactContract,
    CampaignDefinition,
    CampaignExecutionResult,
    CampaignMilestone,
    CampaignPhase,
    CampaignStatus,
    CompensatingAction,
    CompensatingActionType,
    DataflowBinding,
    DataflowChannelType,
    PhaseStatus,
    SagaStep,
    SagaStepStatus,
    MAX_CAMPAIGN_PHASES,
    MAX_DATAFLOW_BINDINGS,
)
from core.provenance import wrap_tainted


def test_artifact_contract_validation_and_serialization():
    contract = ArtifactContract(
        contract_id="c_doc_1",
        expected_artifact_type=ArtifactType.DOCUMENT,
        required_mime_types=("text/markdown", "text/plain"),
        min_quality_score=0.85,
        allow_tainted=False,
        schema_definition={"required_keys": ["summary", "sections"]},
        max_size_bytes=1048576,
        allowed_versions=(1, 2),
        producer_role_id="lead_researcher",
        metadata={"domain": "biomedical", "is_admin": True},  # is_admin should be sanitized
    )

    assert contract.contract_id == "c_doc_1"
    assert contract.expected_artifact_type == ArtifactType.DOCUMENT
    assert "is_admin" not in contract.metadata
    assert contract.metadata.get("domain") == "biomedical"

    d = contract.to_dict()
    assert d["expected_artifact_type"] == "document"
    restored = ArtifactContract.from_dict(d)
    assert restored.contract_id == contract.contract_id
    assert restored.min_quality_score == 0.85


def test_artifact_contract_invalid_inputs():
    with pytest.raises(ValueError, match="contract_id must be a non-empty string"):
        ArtifactContract(contract_id="  ", expected_artifact_type=ArtifactType.CODE)

    with pytest.raises(TypeError, match="expected_artifact_type must be an ArtifactType"):
        ArtifactContract(contract_id="c1", expected_artifact_type=123)  # type: ignore

    with pytest.raises(ValueError, match="min_quality_score"):
        ArtifactContract(contract_id="c1", expected_artifact_type=ArtifactType.CODE, min_quality_score=1.5)


def test_dataflow_binding_creation_and_serialization():
    contract = ArtifactContract(
        contract_id="c_dataset",
        expected_artifact_type=ArtifactType.DATASET,
    )
    binding = DataflowBinding(
        binding_id="bind_1_2",
        source_goal_id="goal_1",
        target_goal_id="goal_2",
        source_artifact_name="metrics.json",
        target_input_key="baseline_metrics",
        channel_type=DataflowChannelType.DIRECT,
        contract=contract,
        is_optional=False,
    )

    assert binding.binding_id == "bind_1_2"
    assert binding.source_goal_id == "goal_1"
    assert binding.target_goal_id == "goal_2"

    d = binding.to_dict()
    assert d["channel_type"] == "direct"
    restored = DataflowBinding.from_dict(d)
    assert restored.binding_id == binding.binding_id
    assert restored.contract is not None
    assert restored.contract.expected_artifact_type == ArtifactType.DATASET


def test_compensating_action_and_saga_step():
    comp_act = CompensatingAction(
        action_id="comp_1",
        action_type=CompensatingActionType.TOMBSTONE_ARTIFACT,
        target_id="art_12345",
        parameters={"reason": "Test rollback"},
        requires_approval=False,
    )
    assert comp_act.action_type == CompensatingActionType.TOMBSTONE_ARTIFACT

    step = SagaStep(
        step_id="step_1",
        goal_id="g1",
        phase_id="p1",
        status=SagaStepStatus.FORWARD_EXECUTED,
        forward_execution_result={"output": "success"},
        compensating_actions=(comp_act,),
    )
    assert step.status == SagaStepStatus.FORWARD_EXECUTED
    assert len(step.compensating_actions) == 1

    upd = step.with_status(SagaStepStatus.COMPENSATED, compensated_at=123456.0)
    assert upd.status == SagaStepStatus.COMPENSATED
    assert upd.compensated_at == 123456.0

    d = step.to_dict()
    restored = SagaStep.from_dict(d)
    assert restored.step_id == step.step_id
    assert len(restored.compensating_actions) == 1


def test_campaign_milestone_and_phase():
    ms = CampaignMilestone(
        milestone_id="ms_1",
        title="Architecture Review Gate",
        criteria=("All modules typed", "No cyclic dependencies"),
        min_evaluation_score=0.9,
    )
    assert ms.min_evaluation_score == 0.9
    assert not ms.is_verified

    verified_ms = ms.with_verification(is_verified=True, score=0.95)
    assert verified_ms.is_verified
    assert verified_ms.evaluation_score == 0.95

    phase = CampaignPhase(
        phase_id="phase_arch",
        name="Architecture Design Phase",
        goal_ids=("goal_arch_1", "goal_arch_2"),
        depends_on_phase_ids=(),
        milestones=(ms,),
        max_concurrency=4,
        status=PhaseStatus.PENDING,
    )
    assert phase.name == "Architecture Design Phase"
    assert len(phase.goal_ids) == 2

    upd_phase = phase.with_status(PhaseStatus.RUNNING, started_at=100.0)
    assert upd_phase.status == PhaseStatus.RUNNING
    assert upd_phase.started_at == 100.0

    d = phase.to_dict()
    restored = CampaignPhase.from_dict(d)
    assert restored.phase_id == phase.phase_id
    assert len(restored.milestones) == 1


def test_campaign_definition_validation_and_serialization():
    p1 = CampaignPhase(phase_id="p1", name="Phase 1", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="Phase 2", goal_ids=("g2",), depends_on_phase_ids=("p1",))
    b1 = DataflowBinding(
        binding_id="b1",
        source_goal_id="g1",
        target_goal_id="g2",
        source_artifact_name="doc.md",
        target_input_key="upstream_doc",
    )

    campaign = CampaignDefinition(
        campaign_id="camp_omega",
        title="Omega Mission Campaign",
        description="Autonomous distributed mission",
        phases=(p1, p2),
        dataflows=(b1,),
        session_id="sess_123",
        budget_tokens=50000,
        max_total_time_seconds=7200.0,
        auto_compensate_on_failure=True,
    )

    assert campaign.campaign_id == "camp_omega"
    assert len(campaign.phases) == 2
    assert campaign.get_phase("p1") == p1
    assert campaign.get_phase("non_existent") is None

    d = campaign.to_dict()
    restored = CampaignDefinition.from_dict(d)
    assert restored.campaign_id == campaign.campaign_id
    assert len(restored.phases) == 2
    assert len(restored.dataflows) == 1


def test_campaign_execution_result_serialization():
    res = CampaignExecutionResult(
        campaign_id="camp_1",
        status=CampaignStatus.COMPLETED,
        completed_phases=("p1", "p2"),
        failed_phases=(),
        compensated_phases=(),
        produced_artifact_ids=("art_1", "art_2"),
        milestone_scores={"ms_1": 0.95},
        total_duration_seconds=12.5,
    )
    assert res.status == CampaignStatus.COMPLETED
    assert len(res.completed_phases) == 2

    d = res.to_dict()
    restored = CampaignExecutionResult.from_dict(d)
    assert restored.campaign_id == res.campaign_id
    assert restored.status == CampaignStatus.COMPLETED


def test_campaign_metadata_security_sanitization():
    forbidden_input = {
        "is_admin": True,
        "is_authorized": True,
        "approved": True,
        "bypass_policy": True,
        "sudo": True,
        "valid_tag": "research_run",
    }
    ms = CampaignMilestone(milestone_id="m1", title="M1", criteria=(), metadata=forbidden_input)
    assert "is_admin" not in ms.metadata
    assert "approved" not in ms.metadata
    assert ms.metadata.get("valid_tag") == "research_run"

    phase = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",), metadata=forbidden_input)
    assert "is_admin" not in phase.metadata
    assert phase.metadata.get("valid_tag") == "research_run"

    comp_act = CompensatingAction(
        action_id="act1",
        action_type=CompensatingActionType.RELEASE_LOCKS,
        target_id="g1",
        parameters=forbidden_input,
        metadata=forbidden_input,
    )
    assert "is_admin" not in comp_act.parameters
    assert "bypass_policy" not in comp_act.metadata


def test_campaign_resource_bounds_enforcement():
    # Exceeding maximum phases in a campaign
    phases = [CampaignPhase(phase_id=f"p_{i}", name=f"P {i}", goal_ids=(f"g_{i}",)) for i in range(MAX_CAMPAIGN_PHASES + 1)]
    with pytest.raises(ValueError, match="phases count .* exceeds limit"):
        CampaignDefinition(campaign_id="c_huge", title="Huge", description="", phases=tuple(phases))

    # Exceeding maximum dataflow bindings
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    bindings = [
        DataflowBinding(
            binding_id=f"b_{i}",
            source_goal_id="g1",
            target_goal_id="g2",
            source_artifact_name="art",
            target_input_key="inp",
        )
        for i in range(MAX_DATAFLOW_BINDINGS + 1)
    ]
    with pytest.raises(ValueError, match="dataflows count .* exceeds limit"):
        CampaignDefinition(campaign_id="c_bindings", title="Bindings", description="", phases=(p1,), dataflows=tuple(bindings))
