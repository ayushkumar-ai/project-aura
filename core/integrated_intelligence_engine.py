"""M40 — Integrated Personal Intelligence Engine for Project AURA.

Ties together memory, knowledge graph, multi-source RAG, context personalization,
hierarchical planning, tool & device ecosystems, experience distillation, proactivity,
and cross-device state synchronization into a unified autonomous execution lifecycle.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.context_personalization_engine import ContextPersonalizationEngine
from core.cross_device_sync_engine import CrossDeviceSyncEngine
from core.cross_device_types import SyncOperationType
from core.device_integration_engine import DeviceIntegrationEngine
from core.durable_state_store import DurablePersonalStateStore
from core.integrated_intelligence_types import (
    LifecycleStage,
    LifecycleTraceRecord,
    UnifiedCycleResult,
)
from core.learning_loop_engine import ExperienceLearningEngine
from core.learning_loop_types import InteractionOutcome
from core.multimodal_engine import MultimodalProcessor
from core.multimodal_types import MultimodalRequest
from core.personal_state_types import MemoryCategory, UserPreferences
from core.proactive_engine import ProactiveAssistanceEngine
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import RetrievalQuery
from core.structured_plan_types import StructuredPlan
from core.structured_planner import StructuredPlanningEngine
from core.tool_ecosystem import ToolEcosystemRegistry
from core.tool_ecosystem_types import ToolExecutionRequest

logger = logging.getLogger("aura.integrated_intelligence")


class IntegratedPersonalIntelligenceEngine:
    """The central unified coordinator executing the complete AURA Personal Intelligence lifecycle."""

    def __init__(
        self,
        durable_state_store: DurablePersonalStateStore | None = None,
        retrieval_pipeline: AdvancedRetrievalPipeline | None = None,
        context_engine: ContextPersonalizationEngine | None = None,
        structured_planner: StructuredPlanningEngine | None = None,
        tool_ecosystem: ToolEcosystemRegistry | None = None,
        proactive_engine: ProactiveAssistanceEngine | None = None,
        learning_engine: ExperienceLearningEngine | None = None,
        multimodal_processor: MultimodalProcessor | None = None,
        device_engine: DeviceIntegrationEngine | None = None,
        cross_device_sync: CrossDeviceSyncEngine | None = None,
        policy_engine: Any | None = None,
        model_router: Any | None = None,
    ):
        # 1. State Store
        self.durable_state_store = durable_state_store or DurablePersonalStateStore()

        # 2. Retrieval & RAG
        self.retrieval_pipeline = retrieval_pipeline or AdvancedRetrievalPipeline(
            durable_state_store=self.durable_state_store,
        )

        # 3. Context & Personalization
        self.context_engine = context_engine or ContextPersonalizationEngine()

        # 4. Tool Ecosystem
        self.tool_ecosystem = tool_ecosystem or ToolEcosystemRegistry(policy_engine=policy_engine)

        # 5. Planning
        self.structured_planner = structured_planner or StructuredPlanningEngine(
            default_tool_executor=self.tool_ecosystem,
            policy_engine=policy_engine,
        )

        # 6. Proactivity
        self.proactive_engine = proactive_engine or ProactiveAssistanceEngine(policy_engine=policy_engine)

        # 7. Learning Loop
        self.learning_engine = learning_engine or ExperienceLearningEngine(
            durable_state_store=self.durable_state_store,
        )

        # 8. Multimodal
        self.multimodal_processor = multimodal_processor or MultimodalProcessor()

        # 9. Device Integration
        self.device_engine = device_engine or DeviceIntegrationEngine(policy_engine=policy_engine)

        # 10. Cross-Device Sync
        self.cross_device_sync = cross_device_sync or CrossDeviceSyncEngine(
            durable_state_store=self.durable_state_store,
        )

        self.policy_engine = policy_engine
        self.model_router = model_router
        self._lock = threading.RLock()

    def execute_autonomous_cycle(
        self,
        input_request: str | MultimodalRequest | None = None,
        task_id: str | None = None,
        auto_sync: bool = True,
        user_input: str | MultimodalRequest | None = None,
    ) -> UnifiedCycleResult:
        """Execute the end-to-end integrated personal intelligence lifecycle."""
        raw_input = input_request if input_request is not None else user_input
        if raw_input is None:
            raw_input = ""

        cycle_start = time.time()
        cycle_id = task_id or f"cycle_{uuid4().hex[:12]}"
        trace: list[LifecycleTraceRecord] = []

        tools_used: list[str] = []
        devices_acted_on: list[str] = []

        # =========================================================================
        # Stage 1: Ingestion & Multimodal Processing
        # =========================================================================
        t0 = time.time()
        if isinstance(raw_input, MultimodalRequest):
            multi_res = self.multimodal_processor.process_request(raw_input)
            effective_prompt = f"{raw_input.prompt}\n{multi_res.extracted_text}".strip()
            block_filenames = [b.filename for b in raw_input.blocks if b.filename]
            trace.append(
                LifecycleTraceRecord(
                    stage=LifecycleStage.INGESTION,
                    timestamp=t0,
                    duration_seconds=time.time() - t0,
                    status="multimodal_processed",
                    details={"modalities": multi_res.detected_modalities, "filenames": block_filenames},
                )
            )
        else:
            effective_prompt = str(raw_input).strip()
            trace.append(
                LifecycleTraceRecord(
                    stage=LifecycleStage.INGESTION,
                    timestamp=t0,
                    duration_seconds=time.time() - t0,
                    status="text_ingested",
                    details={"prompt_length": len(effective_prompt)},
                )
            )

        # =========================================================================
        # Stage 2: Retrieval & RAG across Memory, Knowledge, Experiences
        # =========================================================================
        t0 = time.time()
        rag_bundle = self.retrieval_pipeline.execute_rag(effective_prompt)
        trace.append(
            LifecycleTraceRecord(
                stage=LifecycleStage.RETRIEVAL_RAG,
                timestamp=t0,
                duration_seconds=time.time() - t0,
                status="rag_completed",
                details={
                    "candidates_count": len(rag_bundle.candidates),
                    "sources": rag_bundle.source_counts,
                },
            )
        )

        # =========================================================================
        # Stage 3: Context & Personalization Assembly
        # =========================================================================
        t0 = time.time()
        user_prefs = self.durable_state_store.get_preferences()
        context_bundle = self.context_engine.build_context_bundle(
            user_prompt=effective_prompt,
            user_preferences=user_prefs,
            retrieval_bundle=rag_bundle,
            request_id=cycle_id,
        )
        trace.append(
            LifecycleTraceRecord(
                stage=LifecycleStage.CONTEXT_ASSEMBLY,
                timestamp=t0,
                duration_seconds=time.time() - t0,
                status="context_assembled",
                details={
                    "included_items": len(context_bundle.included_items),
                    "total_chars": context_bundle.total_characters,
                },
            )
        )

        # =========================================================================
        # Stage 4: Structured Planning
        # =========================================================================
        t0 = time.time()
        plan = self.structured_planner.create_plan(goal=effective_prompt)
        trace.append(
            LifecycleTraceRecord(
                stage=LifecycleStage.PLANNING,
                timestamp=t0,
                duration_seconds=time.time() - t0,
                status="plan_created",
                details={"plan_id": plan.plan_id, "steps_count": len(plan.steps)},
            )
        )

        # =========================================================================
        # Stage 5 & 6: Policy Verification & Plan Execution
        # =========================================================================
        t0 = time.time()
        plan_audit = self.structured_planner.execute_plan(
            plan=plan,
            tool_executor=self.tool_ecosystem,
            policy=self.policy_engine,
        )
        for s in plan.steps:
            if s.tool_name:
                tools_used.append(s.tool_name)

        trace.append(
            LifecycleTraceRecord(
                stage=LifecycleStage.ACTION_EXECUTION,
                timestamp=t0,
                duration_seconds=time.time() - t0,
                status="executed" if plan_audit.is_success else "failed",
                details={
                    "steps_completed": plan_audit.steps_completed,
                    "steps_failed": plan_audit.steps_failed,
                },
            )
        )

        # =========================================================================
        # Stage 7 & 8: Outcome Evaluation & Experience Distillation
        # =========================================================================
        t0 = time.time()
        outcome = InteractionOutcome(
            interaction_id=f"int_{cycle_id}",
            task_pattern=effective_prompt.split()[0].lower() if effective_prompt else "general",
            input_prompt=effective_prompt,
            plan_id=plan.plan_id,
            tool_sequence=tools_used,
            success=plan_audit.is_success,
            latency_seconds=time.time() - cycle_start,
        )
        distilled_h = self.learning_engine.record_interaction(outcome)
        trace.append(
            LifecycleTraceRecord(
                stage=LifecycleStage.DISTILLATION,
                timestamp=t0,
                duration_seconds=time.time() - t0,
                status="distilled",
                details={
                    "heuristic_id": distilled_h.heuristic_id,
                    "confidence": distilled_h.confidence,
                    "status": distilled_h.status.value,
                },
            )
        )

        # Record conversational memory in durable state
        self.durable_state_store.record_memory(
            category=MemoryCategory.CONVERSATIONAL,
            content=f"Executed task '{effective_prompt[:60]}' -> Success: {plan_audit.is_success}",
            tags=["autonomous_cycle", "m40"],
        )

        # =========================================================================
        # Stage 9: Cross-Device State Sync
        # =========================================================================
        sync_delta_id = None
        if auto_sync:
            t0 = time.time()
            delta = self.cross_device_sync.generate_delta(
                operation=SyncOperationType.UPDATE_TASK_STATE,
                entity_id=cycle_id,
                payload={"goal": effective_prompt, "status": "completed" if plan_audit.is_success else "failed"},
            )
            sync_delta_id = delta.delta_id
            trace.append(
                LifecycleTraceRecord(
                    stage=LifecycleStage.STATE_SYNC,
                    timestamp=t0,
                    duration_seconds=time.time() - t0,
                    status="synced",
                    details={"delta_id": sync_delta_id},
                )
            )

        total_latency = time.time() - cycle_start
        response_msg = (
            f"AURA autonomous cycle completed successfully for user '{user_prefs.preferred_name}'.\n"
            f"Goal: '{effective_prompt}'\n"
            f"Executed {plan_audit.steps_completed} steps across tools: {', '.join(tools_used) if tools_used else 'none'}."
        )

        if self.model_router is not None and hasattr(self.model_router, "generate_with_fallback") and plan_audit.is_success:
            try:
                from uuid import UUID
                req_uuid = UUID(hex=cycle_id[6:]) if (cycle_id.startswith("cycle_") and len(cycle_id) == 18) else uuid4()
                model_resp, _ = self.model_router.generate_with_fallback(
                    prompt=f"Summarize completion of goal '{effective_prompt}' for user '{user_prefs.preferred_name}'.",
                    request_id=req_uuid,
                )
                if hasattr(model_resp, "content") and model_resp.content:
                    response_msg = model_resp.content
            except Exception as e:
                logger.debug(f"Model router cycle synthesis skipped: {e}")

        return UnifiedCycleResult(
            cycle_id=cycle_id,
            user_prompt=effective_prompt,
            user_name=user_prefs.preferred_name,
            response_content=response_msg,
            is_success=plan_audit.is_success,
            plan_id=plan.plan_id,
            steps_executed=plan_audit.steps_completed,
            tools_used=tools_used,
            devices_acted_on=devices_acted_on,
            distilled_heuristic_id=distilled_h.heuristic_id if distilled_h else None,
            sync_delta_id=sync_delta_id,
            total_duration_seconds=total_latency,
            lifecycle_trace=trace,
            metadata={"cycle_timestamp": cycle_start},
        )
