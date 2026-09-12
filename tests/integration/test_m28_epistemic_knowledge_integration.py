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
from core.skill_types import SynthesizedSkill


def test_m28_agentic_runtime_wiring_and_facade_integration():
    """Verify Epistemic Graph integration in AgenticRuntime facade."""
    runtime = AgenticRuntime()

    assert runtime.epistemic_graph is not None
    assert runtime.experience_distiller is not None
    assert runtime.epistemic_query_engine is not None

    # Register dynamic skill -> auto-distillation
    skill = SynthesizedSkill(
        name="web_scraper_integ",
        description="Scrapes web pages securely",
        source_code="def execute(url): return url",
        ast_hash="test_hash_web_scraper_integ",
        required_capabilities=("web_scraping", "html_parsing"),
        author_role_id="crawler",
    )
    runtime.register_dynamic_skill(skill)

    # Verify skill entity in graph
    assert runtime.epistemic_graph.has_entity("skill_web_scraper_integ")

    # Query matching skills via semantic recommendation engine
    recs = runtime.recommend_skills(required_capabilities=("web_scraping",))
    assert len(recs) >= 1
    assert recs[0]["skill_id"] == "skill_web_scraper_integ"


def test_m28_distillation_and_remediation_query_integration():
    """Verify self-healing event distillation into epistemic knowledge graph."""
    runtime = AgenticRuntime()

    fdr = FaultDiagnosticReport(
        report_id="fdr_db_integ_01",
        campaign_id="camp_db_integ",
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
        action_id="ra_db_retry_integ",
        action_type=RemediationActionType.RETRY_PHASE,
        target_id="p_query",
        tier=1,
        requires_approval=False,
    )
    plan = RemediationPlan(
        plan_id="plan_db_retry_integ",
        fault_report_id="fdr_db_integ_01",
        campaign_id="camp_db_integ",
        phase_id="p_query",
        actions=(action,),
        budget=HealingBudget(),
        created_at=time.time(),
    )
    res = SelfHealingResult(
        result_id="shr_db_integ_01",
        plan_id="plan_db_retry_integ",
        campaign_id="camp_db_integ",
        phase_id="p_query",
        status=HealingStatus.RECOVERED,
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        actions_attempted=1,
        actions_succeeded=1,
        campaign_resumed=True,
        duration_seconds=0.05,
        error=None,
        attempt_number=1,
        timestamp=time.time(),
        metadata={"remediation_summary": "Retried successfully after pool refresh"},
    )

    runtime.experience_distiller.distill_healing_event(fdr, plan=plan, result=res)

    # Query recommended remediation recipes
    recipes = runtime.recommend_remediation(FaultCategory.TRANSIENT_INFRASTRUCTURE)
    assert len(recipes) >= 1
    assert recipes[0]["recipe_id"] == "recipe_plan_db_retry_integ"
    assert recipes[0]["is_recovered"] is True
