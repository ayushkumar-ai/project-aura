"""Integration tests for Milestone 28 Epistemic Knowledge Graph & Experience Distillation."""

import pytest
import time
from unittest.mock import MagicMock

from core.agentic_runtime import AgenticRuntime
from core.campaign_types import CampaignExecutionResult, CampaignStatus
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_types import EntityType, KnowledgeEntity, KnowledgeGraphQuery, KnowledgeRelation, RelationType
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
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.skill_types import SynthesizedSkill


def test_m28_agentic_runtime_wiring_and_facade():
    runtime = AgenticRuntime()

    assert runtime.epistemic_graph is not None
    assert runtime.experience_distiller is not None
    assert runtime.epistemic_query_engine is not None
    assert runtime.checkpoint_manager.epistemic_graph is runtime.epistemic_graph

    # Register a dynamic skill -> verify auto-distillation
    skill = SynthesizedSkill(
        name="web_scraper",
        description="Scrapes web pages securely",
        source_code="def execute(url): return url",
        ast_hash="test_hash_web_scraper",
        required_capabilities=("web_scraping", "html_parsing"),
        author_role_id="crawler",
    )
    runtime.register_dynamic_skill(skill)

    # Check that skill is in the knowledge graph
    assert runtime.epistemic_graph.has_entity("skill_web_scraper")

    # Test recommend_skills via runtime facade
    recs = runtime.recommend_skills(required_capabilities=("web_scraping",))
    assert len(recs) >= 1
    assert recs[0]["skill_id"] == "skill_web_scraper"


def test_m28_distillation_and_remediation_query_loop():
    runtime = AgenticRuntime()

    # 1. Distill a self-healing event
    fdr = FaultDiagnosticReport(
        report_id="fdr_db_01",
        campaign_id="camp_db",
        phase_id="p_query",
        goal_id="g_query",
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        confidence_level=ConfidenceLevel.HIGH,
        culpability_score=0.9,
        error_message="DB connection pool exhausted",
        error_traceback="",
        root_cause_span_id=None,
        affected_artifact_ids=(),
        failing_input="",
        evidence_items=(),
        timestamp=time.time(),
    )

    action = RemediationAction(
        action_id="ra_db_retry",
        action_type=RemediationActionType.RETRY_PHASE,
        target_id="p_query",
        tier=1,
        requires_approval=False,
    )
    plan = RemediationPlan(
        plan_id="plan_db_retry",
        fault_report_id="fdr_db_01",
        campaign_id="camp_db",
        phase_id="p_query",
        actions=(action,),
        budget=HealingBudget(),
        created_at=time.time(),
    )
    res = SelfHealingResult(
        result_id="shr_db_01",
        plan_id="plan_db_retry",
        campaign_id="camp_db",
        phase_id="p_query",
        status=HealingStatus.RECOVERED,
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        actions_attempted=1,
        actions_succeeded=1,
        campaign_resumed=True,
        duration_seconds=0.8,
        error=None,
        attempt_number=1,
        timestamp=time.time(),
    )

    runtime.experience_distiller.distill_healing_event(fdr, plan=plan, result=res)

    # 2. Query recommendation for the fault category
    recs = runtime.recommend_remediation(FaultCategory.TRANSIENT_INFRASTRUCTURE)
    assert len(recs) >= 1
    assert recs[0]["recipe_id"] == "recipe_plan_db_retry"
    assert recs[0]["is_recovered"] is True


def test_m28_checkpoint_persistence_and_recovery(tmp_path):
    # Setup graph with entity and relation
    ekg = EpistemicKnowledgeGraph()
    e1 = KnowledgeEntity(entity_id="e_persist_1", entity_type=EntityType.GOAL, name="Goal 1")
    e2 = KnowledgeEntity(entity_id="e_persist_2", entity_type=EntityType.ROLE, name="Role 2")
    ekg.add_entity(e1)
    ekg.add_entity(e2)
    ekg.add_relation(KnowledgeRelation("r_persist_1", "e_persist_1", "e_persist_2", RelationType.DELEGATED_TO))

    ckpt_mgr1 = RuntimeCheckpointManager(
        checkpoint_dir=tmp_path / "checkpoints",
        epistemic_graph=ekg,
    )
    meta = ckpt_mgr1.save_checkpoint("ckpt_m28_test")
    assert meta.checkpoint_id == "ckpt_m28_test"

    # Restore in new graph instance
    new_ekg = EpistemicKnowledgeGraph()
    ckpt_mgr2 = RuntimeCheckpointManager(
        checkpoint_dir=tmp_path / "checkpoints",
        epistemic_graph=new_ekg,
    )
    ckpt_mgr2.restore_latest_checkpoint()

    assert new_ekg.has_entity("e_persist_1")
    assert new_ekg.has_entity("e_persist_2")
    assert new_ekg.get_relation("r_persist_1") is not None
    assert len(new_ekg.get_outgoing_relations("e_persist_1")) == 1
