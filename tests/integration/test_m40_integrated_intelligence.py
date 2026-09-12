"""Integration tests for M40 Integrated Personal Intelligence and M29-M40 Facade."""

from app.aura import AURA
from app.main import create_aura
from core.integrated_intelligence_engine import IntegratedPersonalIntelligenceEngine
from core.integrated_intelligence_types import LifecycleStage, UnifiedCycleResult
from core.multimodal_engine import MultimodalProcessor
from core.multimodal_types import MultimodalRequest
from core.personal_state_types import UserPreferences


def test_end_to_end_autonomous_cycle():
    """Test full unified lifecycle execution."""
    engine = IntegratedPersonalIntelligenceEngine()
    result = engine.execute_autonomous_cycle(
        user_input="Deploy microservice and calculate optimal resource limits",
        task_id="cycle_test_1",
    )

    assert isinstance(result, UnifiedCycleResult)
    assert result.cycle_id == "cycle_test_1"
    assert result.is_success is True
    assert result.steps_executed == 3
    assert len(result.lifecycle_trace) >= 6

    stages_present = {t.stage for t in result.lifecycle_trace}
    assert LifecycleStage.INGESTION in stages_present
    assert LifecycleStage.RETRIEVAL_RAG in stages_present
    assert LifecycleStage.CONTEXT_ASSEMBLY in stages_present
    assert LifecycleStage.PLANNING in stages_present
    assert LifecycleStage.ACTION_EXECUTION in stages_present
    assert LifecycleStage.DISTILLATION in stages_present
    assert LifecycleStage.STATE_SYNC in stages_present

    assert result.distilled_heuristic_id is not None
    assert result.sync_delta_id is not None
    assert result.total_duration_seconds >= 0.0


def test_aura_facade_m29_to_m40_methods():
    """Verify all top-level AURA facade methods from M29 to M40."""
    aura = create_aura(agentic=True)

    # M29
    rel_report = aura.validate_release()
    assert rel_report.is_production_ready is True

    # M30
    prefs = aura.get_user_preferences()
    assert prefs.preferred_name is not None
    updated_prefs = aura.update_user_preferences({"preferred_name": "IntegrationUser"})
    assert updated_prefs.preferred_name == "IntegrationUser"

    aura.record_durable_memory(category="semantic", content="User tests integration suite", tags=["test"])
    mems = aura.query_durable_memories(tag="test")
    assert len(mems) >= 1

    # M31
    aura.add_knowledge_document(doc_id="k_test", title="Test KDoc", content="Knowledge for RAG testing")
    rag = aura.retrieve_rag_context(query="RAG testing")
    assert len(rag.candidates) >= 1

    # M32
    ctx = aura.build_personalized_context(user_prompt="Explain RAG testing")
    assert "IntegrationUser" in ctx.assembled_prompt

    # M33
    plan = aura.create_structured_plan(goal="Compile and test project")
    assert len(plan.steps) == 3
    audit = aura.execute_structured_plan(plan)
    assert audit.is_success is True

    # M34
    tool_res = aura.execute_ecosystem_tool("calculator", {"expression": "25 * 4"})
    assert tool_res.success is True
    assert tool_res.output["result"] == 100.0
    tools = aura.list_ecosystem_tools()
    assert len(tools) >= 5

    # M35
    proposals = aura.evaluate_proactive_triggers()
    assert isinstance(proposals, list)

    # M36
    report = aura.get_learning_report()
    assert report.total_interactions >= 0

    # M37
    proc = MultimodalProcessor()
    m_req = MultimodalRequest(
        request_id="facade_multi_1",
        prompt="Describe diagram",
        blocks=[proc.ingest_text("Diagram notes")],
    )
    m_res = aura.process_multimodal_request(m_req)
    assert m_res.request_id == "facade_multi_1"

    # M38
    dev_res = aura.execute_device_action(
        device_id="dev_desktop_primary",
        capability="send_notification",
        parameters={"title": "Integration Test", "message": "All Facade checks passing"},
    )
    assert dev_res.success is True
    devices = aura.list_devices()
    assert len(devices) >= 3

    # M39
    sync_status = aura.get_cross_device_sync_status()
    assert sync_status.is_online is True

    # M40
    cycle_res = aura.execute_integrated_cycle("Perform automated system health audit")
    assert cycle_res.is_success is True
    assert "IntegrationUser" in cycle_res.user_name


def test_multimodal_integrated_cycle():
    """Test integrated cycle with multimodal request input."""
    engine = IntegratedPersonalIntelligenceEngine()
    proc = MultimodalProcessor()

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    img_block = proc.ingest_image(png_bytes, filename="pipeline.png")

    m_req = MultimodalRequest(
        request_id="multi_cycle_1",
        prompt="Inspect architecture diagram and deploy service",
        blocks=[img_block],
    )

    result = engine.execute_autonomous_cycle(input_request=m_req)
    assert result.is_success is True
    assert result.steps_executed >= 1
    assert "pipeline.png" in str(result.lifecycle_trace[0].details)
