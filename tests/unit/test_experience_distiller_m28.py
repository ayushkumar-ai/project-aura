"""Unit tests for Milestone 28 Autonomous Experience Distiller."""

import pytest
import time
from core.campaign_types import CampaignExecutionResult, CampaignStatus
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_types import EntityType, RelationType
from core.experience_distiller import ExperienceDistiller
from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    HealingBudget,
    HealingStatus,
    RemediationAction,
    RemediationActionType,
    RemediationPlan,
    SelfHealingResult,
)
from core.skill_types import SynthesizedSkill


def test_distill_campaign_result():
    graph = EpistemicKnowledgeGraph()
    distiller = ExperienceDistiller(knowledge_graph=graph)

    result = CampaignExecutionResult(
        campaign_id="camp_pipeline_1",
        status=CampaignStatus.COMPLETED,
        completed_phases=("p_extract", "p_transform"),
        failed_phases=(),
        compensated_phases=(),
        produced_artifact_ids=("art_dataset", "art_report"),
        milestone_scores={"m1": 0.95},
        total_duration_seconds=12.5,
    )

    distilled_ids = distiller.distill_campaign_result(result)
    assert len(distilled_ids) >= 3
    assert graph.has_entity("ep_camp_pipeline_1")
    assert graph.has_entity("goal_camp_pipeline_1_p_extract")
    assert graph.has_entity("goal_camp_pipeline_1_p_transform")
    assert graph.has_entity("art_art_dataset")

    # Check relationships
    ep_rels = graph.get_outgoing_relations("ep_camp_pipeline_1", RelationType.DEPENDS_ON)
    assert len(ep_rels) == 2


def test_distill_dynamic_skill():
    graph = EpistemicKnowledgeGraph()
    distiller = ExperienceDistiller(knowledge_graph=graph)

    skill = SynthesizedSkill(
        name="json_cleaner",
        description="Cleans and normalizes JSON payloads",
        source_code="def execute(data): return data",
        ast_hash="test_hash_json_cleaner",
        required_capabilities=("json_parsing", "data_cleaning"),
        author_role_id="data_engineer",
    )

    skill_id = distiller.distill_dynamic_skill(skill)
    assert skill_id == "skill_json_cleaner"
    assert graph.has_entity("skill_json_cleaner")
    assert graph.has_entity("role_data_engineer")

    ent = graph.get_entity("skill_json_cleaner")
    assert ent is not None
    assert "json_parsing" in ent.properties.get("capabilities", [])
    assert ent.confidence >= 0.6


def test_distill_healing_event():
    graph = EpistemicKnowledgeGraph()
    distiller = ExperienceDistiller(knowledge_graph=graph)

    report = FaultDiagnosticReport(
        report_id="fdr_01",
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        confidence_level=ConfidenceLevel.HIGH,
        culpability_score=0.9,
        error_message="Network timeout after 30s",
        error_traceback="",
        root_cause_span_id=None,
        affected_artifact_ids=(),
        failing_input="",
        evidence_items=(),
        timestamp=time.time(),
    )

    action = RemediationAction(
        action_id="ra_1",
        action_type=RemediationActionType.RETRY_PHASE,
        target_id="p1",
        tier=1,
        requires_approval=False,
    )
    plan = RemediationPlan(
        plan_id="plan_01",
        fault_report_id="fdr_01",
        campaign_id="c1",
        phase_id="p1",
        actions=(action,),
        budget=HealingBudget(),
        created_at=time.time(),
    )

    res = SelfHealingResult(
        result_id="shr_01",
        plan_id="plan_01",
        campaign_id="c1",
        phase_id="p1",
        status=HealingStatus.RECOVERED,
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        actions_attempted=1,
        actions_succeeded=1,
        campaign_resumed=True,
        duration_seconds=1.2,
        error=None,
        attempt_number=1,
        timestamp=time.time(),
    )

    fault_id = distiller.distill_healing_event(report, plan=plan, result=res)
    assert graph.has_entity(fault_id)
    assert graph.has_entity("recipe_plan_01")

    # Check RESOLVED_BY link
    rels = graph.get_outgoing_relations(fault_id, RelationType.RESOLVED_BY)
    assert len(rels) == 1
    assert rels[0].target_id == "recipe_plan_01"
