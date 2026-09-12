import logging
import time
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from core.agent_plan import AgentPlan
from core.agent_runtime import AgentRuntime
from core.approval import ApprovalGateway
from core.autonomous_agent import AutonomousAgentExecutor, AutonomousAgentResult
from core.capability_registry import ModelCapability
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_engine import GoalEngine
from core.goal_reasoner import GoalEvaluationResult
from core.goal_store import GoalStore, InMemoryGoalStore
from core.model_router import ModelRouter, TaskRequirements
from core.models import AURARequest, AURAResponse
from core.policy import Policy
from core.skill_registry import SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.task_state import TaskState, TaskStatus
from core.task_state_store import InMemoryTaskStateStore, TaskStateStore
from core.file_task_state_store import FileTaskStateStore
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from core.memory_manager import MemoryManager
from core.meta_policy import MetaPolicyEngine
from core.strategy_lineage import StrategyLineageStore
from core.goal_adapter import GoalAdapter
from core.goal_stagnation import GoalStagnationMonitor
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.goal_scheduler import MultiGoalScheduler
from core.event_dispatcher import ProactiveEventDispatcher
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import ProactiveEvent
from core.daemon_types import (
    CheckpointMetadata,
    DaemonStatus,
    SupervisorConfig,
    SupervisorTelemetry,
)
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.runtime_supervisor import AutonomousSupervisor
from core.session_types import (
    SessionContext,
    SessionMetadata,
    SessionStatus,
    StreamEvent,
    StreamEventType,
    OperatorAction,
    OperatorActionType,
    OperatorResolution,
    validate_session_id,
)
from core.session_store import SessionStore, InMemorySessionStore, FileSessionStore
from core.session_manager import SessionManager
from core.streaming_gateway import StreamingGateway
from core.operator_bridge import OperatorBridge
from core.provider_health import ProviderHealthTracker
from core.resilient_router import ResilientModelRouter
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry
from core.agent_message_types import AgentMessage, AgentMessageType
from core.agent_message_bus import AgentMessageBus
from core.agent_delegation import DelegationContract, DelegationResult, DelegationTree
from core.consensus_engine import ConsensusEngine, ConsensusStrategy
from core.team_types import TeamTopology, TeamMember, TeamDefinition, TeamExecutionResult
from core.team_orchestrator import TeamOrchestrator
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationReport, BenchmarkRunSummary
from core.trace_types import TraceContext, SpanRecord, SpanKind, SpanStatus
from core.tracing import Tracer
from core.trace_exporter import InMemoryTraceExporter, JsonlTraceExporter, CausalExecutionGraph
from core.artifact_types import Artifact, ArtifactType
from core.artifact_store import ArtifactStore, InMemoryArtifactStore, FileWorkspaceArtifactStore
from core.artifact_manager import ArtifactManager, ArtifactLineage
from core.adaptive_optimizer import AdaptivePolicyOptimizer, OptimizationEvent, OptimizationHistory
from core.feedback_bridge import FeedbackBridge
from core.campaign_types import (
    CampaignDefinition,
    CampaignPhase,
    CampaignMilestone,
    CampaignExecutionResult,
    CampaignStatus,
    PhaseStatus,
    DataflowBinding,
    ArtifactContract,
)
from core.campaign_engine import CampaignEngine
from core.mission_graph import MissionGraph
from core.artifact_pipeline import ArtifactPipelineRouter
from core.saga_coordinator import SagaCoordinator
from core.skill_types import (
    SkillLifecycleState,
    SynthesizedSkill,
    DynamicTool,
    CompositeSkill,
    SkillStep,
    TestVector,
    SecurityAuditReport,
    SkillVerificationReport,
)
from core.code_sandbox import CodeSandboxValidator, SandboxedToolExecutor
from core.skill_verification import SkillVerificationHarness
from core.skill_synthesis import SkillSynthesizer
from core.dynamic_skill_registry import DynamicSkillRegistry
from core.fault_types import (
    FaultCategory,
    RemediationActionType,
    HealingStatus,
    ConfidenceLevel,
    HealingBudget,
    FaultDiagnosticReport,
    RemediationAction,
    RemediationPlan,
    SelfHealingResult,
)
from core.causal_fault_analyzer import CausalFaultAnalyzer
from core.remediation_planner import RemediationPlanner
from core.self_healing_orchestrator import SelfHealingOrchestrator
from core.epistemic_types import (
    EntityType,
    RelationType,
    KnowledgeEntity,
    KnowledgeRelation,
    KnowledgeGraphQuery,
    KnowledgeGraphSubgraph,
)
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.experience_distiller import ExperienceDistiller
from core.epistemic_query_engine import EpistemicQueryEngine

from core.release_validator import ReleaseValidator
from core.durable_state_store import DurablePersonalStateStore
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.context_personalization_engine import ContextPersonalizationEngine
from core.structured_planner import StructuredPlanningEngine
from core.tool_ecosystem import ToolEcosystemRegistry
from core.proactive_engine import ProactiveAssistanceEngine
from core.learning_loop_engine import ExperienceLearningEngine
from core.multimodal_engine import MultimodalProcessor
from core.device_integration_engine import DeviceIntegrationEngine
from core.cross_device_sync_engine import CrossDeviceSyncEngine
from core.integrated_intelligence_engine import IntegratedPersonalIntelligenceEngine

from interfaces.model import ModelInterface
from interfaces.tool_executor import ToolExecutor

logger = logging.getLogger("aura.agentic_runtime")


class ExecutionMode(str, Enum):
    """Execution modes supported by the unified AgenticRuntime."""

    STANDARD_WORKFLOW = "standard_workflow"
    AUTONOMOUS_AGENT = "autonomous_agent"
    GOAL_DRIVEN = "goal_driven"
    MULTI_AGENT_TEAM = "multi_agent_team"


class AgenticRuntime:
    """End-to-end agentic coordinator unifying WorkflowExecutor, AutonomousAgentExecutor, and GoalEngine."""

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        model_router: ModelRouter | None = None,
        model: ModelInterface | None = None,
        tool_executor: ToolExecutor | None = None,
        policy: Policy | None = None,
        runtime: AgentRuntime | None = None,
        planner: TaskPlanner | None = None,
        workflow_executor: WorkflowExecutor | None = None,
        autonomous_executor: AutonomousAgentExecutor | None = None,
        goal_store: GoalStore | None = None,
        goal_engine: GoalEngine | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        max_replans: int = 0,
        default_timeout: float | None = None,
        default_mode: ExecutionMode = ExecutionMode.STANDARD_WORKFLOW,
        memory_manager: MemoryManager | None = None,
        reflector: Any | None = None,
        consolidator: Any | None = None,
        calibrator: Any | None = None,
        meta_policy: MetaPolicyEngine | None = None,
        strategy_lineage: StrategyLineageStore | None = None,
        goal_adapter: GoalAdapter | None = None,
        stagnation_monitor: GoalStagnationMonitor | None = None,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        scheduler: MultiGoalScheduler | None = None,
        event_dispatcher: ProactiveEventDispatcher | None = None,
        clarification_gateway: ClarificationGateway | None = None,
        checkpoint_manager: RuntimeCheckpointManager | None = None,
        supervisor: AutonomousSupervisor | None = None,
        supervisor_config: SupervisorConfig | None = None,
        session_store: SessionStore | None = None,
        session_manager: SessionManager | None = None,
        streaming_gateway: StreamingGateway | None = None,
        operator_bridge: OperatorBridge | None = None,
        health_tracker: ProviderHealthTracker | None = None,
        resilient_router: ResilientModelRouter | None = None,
        role_registry: RoleRegistry | None = None,
        team_orchestrator: TeamOrchestrator | None = None,
        message_bus: AgentMessageBus | None = None,
        consensus_engine: ConsensusEngine | None = None,
        evaluation_engine: EvaluationEngine | None = None,
        tracer: Tracer | None = None,
        trace_exporter: InMemoryTraceExporter | None = None,
        artifact_store: ArtifactStore | None = None,
        artifact_manager: ArtifactManager | None = None,
        adaptive_optimizer: AdaptivePolicyOptimizer | None = None,
        feedback_bridge: FeedbackBridge | None = None,
        campaign_engine: CampaignEngine | None = None,
        dynamic_skill_registry: DynamicSkillRegistry | None = None,
        skill_synthesizer: SkillSynthesizer | None = None,
        sandbox_validator: CodeSandboxValidator | None = None,
        verification_harness: SkillVerificationHarness | None = None,
        causal_fault_analyzer: CausalFaultAnalyzer | None = None,
        remediation_planner: RemediationPlanner | None = None,
        self_healing_orchestrator: SelfHealingOrchestrator | None = None,
        epistemic_graph: EpistemicKnowledgeGraph | None = None,
        experience_distiller: ExperienceDistiller | None = None,
        epistemic_query_engine: EpistemicQueryEngine | None = None,
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
        integrated_intelligence_engine: IntegratedPersonalIntelligenceEngine | None = None,
        release_validator: ReleaseValidator | None = None,
    ):
        if skill_registry is not None and not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry or None.")
        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")
        if runtime is not None and not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime or None.")
        if workflow_executor is not None and not isinstance(workflow_executor, WorkflowExecutor):
            raise TypeError("workflow_executor must be an instance of WorkflowExecutor or None.")
        if autonomous_executor is not None and not isinstance(autonomous_executor, AutonomousAgentExecutor):
            raise TypeError("autonomous_executor must be an instance of AutonomousAgentExecutor or None.")
        if goal_store is not None and not isinstance(goal_store, GoalStore):
            raise TypeError("goal_store must be an instance of GoalStore or None.")
        if goal_engine is not None and not isinstance(goal_engine, GoalEngine):
            raise TypeError("goal_engine must be an instance of GoalEngine or None.")
        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")
        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")
        if model_router is not None and not isinstance(model_router, ModelRouter):
            raise TypeError("model_router must be an instance of ModelRouter or None.")
        if model is not None and not isinstance(model, ModelInterface):
            raise TypeError("model must be an instance of ModelInterface or None.")
        if tool_executor is not None and not isinstance(tool_executor, ToolExecutor):
            raise TypeError("tool_executor must be an instance of ToolExecutor or None.")
        if policy is not None and not isinstance(policy, Policy):
            raise TypeError("policy must be an instance of Policy or None.")
        if memory_manager is not None and not isinstance(memory_manager, MemoryManager):
            raise TypeError("memory_manager must be an instance of MemoryManager or None.")
        if checkpoint_manager is not None and not isinstance(checkpoint_manager, RuntimeCheckpointManager):
            raise TypeError("checkpoint_manager must be an instance of RuntimeCheckpointManager or None.")
        if supervisor is not None and not isinstance(supervisor, AutonomousSupervisor):
            raise TypeError("supervisor must be an instance of AutonomousSupervisor or None.")
        if supervisor_config is not None and not isinstance(supervisor_config, SupervisorConfig):
            raise TypeError("supervisor_config must be an instance of SupervisorConfig or None.")
        if session_store is not None and not isinstance(session_store, SessionStore):
            raise TypeError("session_store must be an instance of SessionStore or None.")
        if session_manager is not None and not isinstance(session_manager, SessionManager):
            raise TypeError("session_manager must be an instance of SessionManager or None.")
        if streaming_gateway is not None and not isinstance(streaming_gateway, StreamingGateway):
            raise TypeError("streaming_gateway must be an instance of StreamingGateway or None.")
        if operator_bridge is not None and not isinstance(operator_bridge, OperatorBridge):
            raise TypeError("operator_bridge must be an instance of OperatorBridge or None.")
        if health_tracker is not None and not isinstance(health_tracker, ProviderHealthTracker):
            raise TypeError("health_tracker must be an instance of ProviderHealthTracker or None.")
        if resilient_router is not None and not isinstance(resilient_router, ResilientModelRouter):
            raise TypeError("resilient_router must be an instance of ResilientModelRouter or None.")
        if role_registry is not None and not isinstance(role_registry, RoleRegistry):
            raise TypeError("role_registry must be an instance of RoleRegistry or None.")
        if team_orchestrator is not None and not isinstance(team_orchestrator, TeamOrchestrator):
            raise TypeError("team_orchestrator must be an instance of TeamOrchestrator or None.")
        if message_bus is not None and not isinstance(message_bus, AgentMessageBus):
            raise TypeError("message_bus must be an instance of AgentMessageBus or None.")
        if consensus_engine is not None and not isinstance(consensus_engine, ConsensusEngine):
            raise TypeError("consensus_engine must be an instance of ConsensusEngine or None.")
        if campaign_engine is not None and not isinstance(campaign_engine, CampaignEngine):
            raise TypeError("campaign_engine must be an instance of CampaignEngine or None.")
        if dynamic_skill_registry is not None and not isinstance(dynamic_skill_registry, DynamicSkillRegistry):
            raise TypeError("dynamic_skill_registry must be an instance of DynamicSkillRegistry or None.")
        if skill_synthesizer is not None and not isinstance(skill_synthesizer, SkillSynthesizer):
            raise TypeError("skill_synthesizer must be an instance of SkillSynthesizer or None.")
        if sandbox_validator is not None and not isinstance(sandbox_validator, CodeSandboxValidator):
            raise TypeError("sandbox_validator must be an instance of CodeSandboxValidator or None.")
        if verification_harness is not None and not isinstance(verification_harness, SkillVerificationHarness):
            raise TypeError("verification_harness must be an instance of SkillVerificationHarness or None.")
        if causal_fault_analyzer is not None and not isinstance(causal_fault_analyzer, CausalFaultAnalyzer):
            raise TypeError("causal_fault_analyzer must be an instance of CausalFaultAnalyzer or None.")
        if remediation_planner is not None and not isinstance(remediation_planner, RemediationPlanner):
            raise TypeError("remediation_planner must be an instance of RemediationPlanner or None.")
        if self_healing_orchestrator is not None and not isinstance(self_healing_orchestrator, SelfHealingOrchestrator):
            raise TypeError("self_healing_orchestrator must be an instance of SelfHealingOrchestrator or None.")
        if epistemic_graph is not None and not isinstance(epistemic_graph, EpistemicKnowledgeGraph):
            raise TypeError("epistemic_graph must be an instance of EpistemicKnowledgeGraph or None.")
        if experience_distiller is not None and not isinstance(experience_distiller, ExperienceDistiller):
            raise TypeError("experience_distiller must be an instance of ExperienceDistiller or None.")
        if epistemic_query_engine is not None and not isinstance(epistemic_query_engine, EpistemicQueryEngine):
            raise TypeError("epistemic_query_engine must be an instance of EpistemicQueryEngine or None.")
        if not isinstance(max_replans, int) or max_replans < 0:
            raise ValueError("max_replans must be a non-negative integer.")
        if isinstance(default_mode, str):
            default_mode = ExecutionMode(default_mode)
        elif not isinstance(default_mode, ExecutionMode):
            raise TypeError("default_mode must be an instance of ExecutionMode.")

        self.memory_manager = memory_manager if memory_manager is not None else MemoryManager()

        from core.agent_reflection import AgentReflector
        from core.memory_consolidation import MemoryConsolidator
        from core.heuristic_calibrator import HeuristicCalibrator

        self.reflector = reflector if reflector is not None else AgentReflector()
        self.calibrator = calibrator if calibrator is not None else HeuristicCalibrator()
        self.consolidator = (
            consolidator
            if consolidator is not None
            else MemoryConsolidator(
                memory_store=getattr(self.memory_manager, "store", None),
                memory_manager=self.memory_manager,
            )
        )
        self.strategy_lineage = strategy_lineage if strategy_lineage is not None else StrategyLineageStore()
        self.meta_policy = (
            meta_policy
            if meta_policy is not None
            else MetaPolicyEngine(
                lineage_store=self.strategy_lineage,
                calibrator=self.calibrator,
            )
        )
        self.stagnation_monitor = (
            stagnation_monitor
            if stagnation_monitor is not None
            else GoalStagnationMonitor(
                lineage_store=self.strategy_lineage,
                meta_policy=self.meta_policy,
            )
        )
        self.budget_manager = budget_manager if budget_manager is not None else ResourceBudgetManager()
        self.lock_manager = lock_manager if lock_manager is not None else SharedResourceLockManager()
        self.clarification_gateway = clarification_gateway if clarification_gateway is not None else ClarificationGateway()
        self.event_dispatcher = event_dispatcher if event_dispatcher is not None else ProactiveEventDispatcher()
        self.scheduler = (
            scheduler
            if scheduler is not None
            else MultiGoalScheduler(
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
            )
        )

        # Determine effective skill_registry
        if skill_registry is None:
            if runtime is not None:
                skill_registry = runtime.skill_registry
            elif planner is not None:
                skill_registry = planner.skill_registry
            elif workflow_executor is not None:
                skill_registry = workflow_executor.runtime.skill_registry
            elif autonomous_executor is not None:
                skill_registry = autonomous_executor.runtime.skill_registry
            else:
                skill_registry = SkillRegistry()

        self.skill_registry = skill_registry
        self.health_tracker = health_tracker if health_tracker is not None else ProviderHealthTracker()
        self.resilient_router = resilient_router
        if self.resilient_router is None and isinstance(model_router, ResilientModelRouter):
            self.resilient_router = model_router
        elif model_router is None and self.resilient_router is not None:
            model_router = self.resilient_router

        self.model_router = model_router
        self.model = model
        self.tool_executor = tool_executor
        self.policy = policy

        # Determine effective state_store (M10/M18)
        if state_store is not None:
            self.state_store = state_store
        else:
            task_dir = getattr(settings, "aura_task_state_storage_dir", "")
            if task_dir:
                self.state_store = FileTaskStateStore(task_dir)
            else:
                self.state_store = InMemoryTaskStateStore()

        self.approval_gateway = approval_gateway
        self.max_replans = max_replans
        self.default_timeout = default_timeout
        self.default_mode = default_mode

        # Resolve or create AgentRuntime
        if runtime is not None:
            self.runtime = runtime
        elif workflow_executor is not None:
            self.runtime = workflow_executor.runtime
        elif autonomous_executor is not None:
            self.runtime = autonomous_executor.runtime
        else:
            self.runtime = AgentRuntime(
                skill_registry=self.skill_registry,
                model_router=self.model_router,
                tool_executor=self.tool_executor,
                policy=self.policy,
                default_timeout=self.default_timeout,
            )

        # Resolve or create TaskPlanner
        if planner is not None:
            self.planner = planner
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        elif workflow_executor is not None and workflow_executor.planner is not None:
            self.planner = workflow_executor.planner
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        elif autonomous_executor is not None:
            self.planner = autonomous_executor.planner
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        else:
            self.planner = TaskPlanner(
                skill_registry=self.skill_registry,
                model=self.model,
                model_router=self.model_router,
                memory_manager=self.memory_manager,
                heuristic_calibrator=self.calibrator,
            )

        # Resolve or create WorkflowExecutor (M8)
        if workflow_executor is not None:
            self.workflow_executor = workflow_executor
        else:
            self.workflow_executor = WorkflowExecutor(
                runtime=self.runtime,
                planner=self.planner,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                max_replans=self.max_replans,
            )

        # Resolve or create AutonomousAgentExecutor (M10)
        if autonomous_executor is not None:
            self.autonomous_executor = autonomous_executor
            if self.autonomous_executor.memory_manager is None:
                self.autonomous_executor.memory_manager = self.memory_manager
            if getattr(self.autonomous_executor, "reflector", None) is None:
                self.autonomous_executor.reflector = self.reflector
            if getattr(self.autonomous_executor, "consolidator", None) is None:
                self.autonomous_executor.consolidator = self.consolidator
            if getattr(self.autonomous_executor, "calibrator", None) is None:
                self.autonomous_executor.calibrator = self.calibrator
        else:
            self.autonomous_executor = AutonomousAgentExecutor(
                runtime=self.runtime,
                planner=self.planner,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                memory_manager=self.memory_manager,
                reflector=self.reflector,
                consolidator=self.consolidator,
                calibrator=self.calibrator,
            )

        # Multi-Session & Streaming Gateway (M19)
        sess_dir = getattr(settings, "aura_session_storage_dir", "")
        if session_store is not None:
            self.session_store = session_store
        elif sess_dir:
            self.session_store = FileSessionStore(sess_dir)
        else:
            self.session_store = InMemorySessionStore()

        self.session_manager = (
            session_manager
            if session_manager is not None
            else SessionManager(
                store=self.session_store,
                default_ttl_seconds=getattr(settings, "aura_session_ttl_seconds", 3600.0),
                max_active_sessions=getattr(settings, "aura_max_active_sessions", 100),
                max_history_turns=getattr(settings, "aura_max_session_history_turns", 100),
            )
        )
        self.streaming_gateway = (
            streaming_gateway
            if streaming_gateway is not None
            else StreamingGateway(
                max_queue_size=getattr(settings, "aura_streaming_queue_max_size", 1000),
                replay_buffer_size=getattr(settings, "aura_streaming_replay_buffer_size", 1000),
                max_payload_chars=getattr(settings, "aura_max_event_payload_chars", 50000),
            )
        )

        # Multi-Agent Team Collaboration & Role Mesh (M21/M22)
        self.role_registry = role_registry if role_registry is not None else RoleRegistry()
        self.message_bus = (
            message_bus
            if message_bus is not None
            else AgentMessageBus(
                max_queue_size=getattr(settings, "aura_message_bus_max_queue_size", 1000),
                max_history_size=getattr(settings, "aura_message_bus_max_history_size", 5000),
            )
        )
        self.consensus_engine = (
            consensus_engine
            if consensus_engine is not None
            else ConsensusEngine()
        )
        self.team_orchestrator = (
            team_orchestrator
            if team_orchestrator is not None
            else TeamOrchestrator(
                role_registry=self.role_registry,
                message_bus=self.message_bus,
                consensus_engine=self.consensus_engine,
                model=self.model,
                model_router=self.model_router,
                tool_executor=self.tool_executor,
                approval_gateway=self.approval_gateway,
                budget_manager=self.budget_manager,
                streaming_gateway=self.streaming_gateway,
                max_team_members=getattr(settings, "aura_max_team_members", 20),
                max_delegation_depth=getattr(settings, "aura_max_delegation_depth", 3),
            )
        )

        # Resolve or create GoalStore & GoalEngine (M11 / M12 / M22)
        if goal_store is not None:
            self.goal_store = goal_store
        elif goal_engine is not None:
            self.goal_store = goal_engine.goal_store
        else:
            self.goal_store = InMemoryGoalStore()

        self.goal_adapter = (
            goal_adapter
            if goal_adapter is not None
            else GoalAdapter(
                meta_policy=self.meta_policy,
                lineage_store=self.strategy_lineage,
                planner=self.planner,
                heuristic_calibrator=self.calibrator,
                clarification_gateway=self.clarification_gateway,
                role_registry=self.role_registry,
            )
        )

        if goal_engine is not None:
            self.goal_engine = goal_engine
            if self.goal_engine.memory_manager is None:
                self.goal_engine.memory_manager = self.memory_manager
            if getattr(self.goal_engine, "heuristic_calibrator", None) is None and self.calibrator is not None:
                self.goal_engine.heuristic_calibrator = self.calibrator
            if getattr(self.goal_engine, "meta_policy", None) is None:
                self.goal_engine.meta_policy = self.meta_policy
            if getattr(self.goal_engine, "strategy_lineage", None) is None:
                self.goal_engine.strategy_lineage = self.strategy_lineage
            if getattr(self.goal_engine, "goal_adapter", None) is None:
                self.goal_engine.goal_adapter = self.goal_adapter
            if getattr(self.goal_engine, "stagnation_monitor", None) is None:
                self.goal_engine.stagnation_monitor = self.stagnation_monitor
            if getattr(self.goal_engine, "team_orchestrator", None) is None:
                self.goal_engine.team_orchestrator = self.team_orchestrator
            if getattr(self.goal_engine, "role_registry", None) is None:
                self.goal_engine.role_registry = self.role_registry
        else:
            self.goal_engine = GoalEngine(
                goal_store=self.goal_store,
                runtime=self.runtime,
                executor=self.autonomous_executor,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                memory_manager=self.memory_manager,
                heuristic_calibrator=self.calibrator,
                meta_policy=self.meta_policy,
                strategy_lineage=self.strategy_lineage,
                goal_adapter=self.goal_adapter,
                stagnation_monitor=self.stagnation_monitor,
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
                scheduler=self.scheduler,
                event_dispatcher=self.event_dispatcher,
                clarification_gateway=self.clarification_gateway,
                team_orchestrator=self.team_orchestrator,
                role_registry=self.role_registry,
            )

        # Runtime Checkpoint Manager & Supervisor Daemon (M18 / M22)
        ckpt_dir = getattr(settings, "aura_checkpoint_dir", ".aura_checkpoints")
        ckpt_ret = getattr(settings, "aura_checkpoint_retention_count", 5)
        self.checkpoint_manager = (
            checkpoint_manager
            if checkpoint_manager is not None
            else RuntimeCheckpointManager(
                checkpoint_dir=ckpt_dir,
                retention_count=ckpt_ret,
                scheduler=self.scheduler,
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
                clarification_gateway=self.clarification_gateway,
                event_dispatcher=self.event_dispatcher,
                delegation_tree=self.team_orchestrator.delegation_tree,
                message_bus=self.message_bus,
                role_registry=self.role_registry,
            )
        )
        self.supervisor_config = (
            supervisor_config
            if supervisor_config is not None
            else SupervisorConfig(
                checkpoint_dir=ckpt_dir,
                checkpoint_retention_count=ckpt_ret,
                shutdown_timeout_seconds=getattr(settings, "aura_daemon_shutdown_timeout_seconds", 5.0),
                heartbeat_interval_seconds=getattr(settings, "aura_daemon_heartbeat_interval_seconds", 1.0),
                scheduler_interval_seconds=getattr(settings, "aura_daemon_scheduler_interval_seconds", 2.0),
                event_interval_seconds=getattr(settings, "aura_daemon_event_interval_seconds", 1.0),
                lock_prune_interval_seconds=getattr(settings, "aura_daemon_lock_prune_interval_seconds", 10.0),
                clarification_interval_seconds=getattr(settings, "aura_daemon_clarification_interval_seconds", 10.0),
                memory_interval_seconds=getattr(settings, "aura_daemon_memory_interval_seconds", 300.0),
                checkpoint_interval_seconds=getattr(settings, "aura_daemon_checkpoint_interval_seconds", 30.0),
            )
        )
        self.supervisor = (
            supervisor
            if supervisor is not None
            else AutonomousSupervisor(
                runtime=self,
                config=self.supervisor_config,
                checkpoint_manager=self.checkpoint_manager,
            )
        )

        self.operator_bridge = (
            operator_bridge
            if operator_bridge is not None
            else OperatorBridge(
                approval_gateway=self.approval_gateway,
                clarification_gateway=self.clarification_gateway,
                streaming_gateway=self.streaming_gateway,
                supervisor=self.supervisor,
                default_operator_timeout=getattr(settings, "aura_operator_timeout_seconds", 300.0),
            )
        )
        self.evaluation_engine = (
            evaluation_engine
            if evaluation_engine is not None
            else EvaluationEngine()
        )

        # M24 Distributed Tracing
        self.tracer = tracer if tracer is not None else Tracer()
        self.trace_exporter = trace_exporter if trace_exporter is not None else InMemoryTraceExporter()
        self.tracer.register_exporter(self.trace_exporter)

        # M24 Versioned Artifact Lifecycle
        if artifact_store is not None:
            self.artifact_store = artifact_store
        else:
            art_dir = getattr(settings, "aura_artifact_storage_dir", "")
            if art_dir:
                self.artifact_store = FileWorkspaceArtifactStore(art_dir)
            else:
                self.artifact_store = InMemoryArtifactStore()

        self.artifact_manager = (
            artifact_manager
            if artifact_manager is not None
            else ArtifactManager(store=self.artifact_store, tracer=self.tracer)
        )

        # M24 Closed-Loop Adaptive Self-Tuning
        self.adaptive_optimizer = (
            adaptive_optimizer
            if adaptive_optimizer is not None
            else AdaptivePolicyOptimizer(
                meta_policy=self.meta_policy,
                calibrator=self.calibrator,
                model_router=self.model_router,
                team_orchestrator=self.team_orchestrator,
                role_registry=self.role_registry,
            )
        )
        self.feedback_bridge = (
            feedback_bridge
            if feedback_bridge is not None
            else FeedbackBridge(
                optimizer=self.adaptive_optimizer,
                event_dispatcher=self.event_dispatcher,
            )
        )

        # M25 Autonomous Mission Campaign Engine
        self.campaign_engine = (
            campaign_engine
            if campaign_engine is not None
            else CampaignEngine(
                goal_engine=self.goal_engine,
                scheduler=self.scheduler,
                team_orchestrator=self.team_orchestrator,
                artifact_manager=self.artifact_manager,
                evaluation_engine=self.evaluation_engine,
                tracer=self.tracer,
                streaming_gateway=self.streaming_gateway,
            )
        )
        if self.checkpoint_manager is not None and getattr(self.checkpoint_manager, "campaign_engine", None) is None:
            self.checkpoint_manager.campaign_engine = self.campaign_engine

        # M26 Dynamic Skill Synthesis, Sandbox & Verification Wiring
        self.sandbox_validator = sandbox_validator if sandbox_validator is not None else CodeSandboxValidator()
        self.sandbox_executor = SandboxedToolExecutor(validator=self.sandbox_validator)
        self.verification_harness = (
            verification_harness
            if verification_harness is not None
            else SkillVerificationHarness(
                validator=self.sandbox_validator,
                executor=self.sandbox_executor,
                trajectory_verifier=getattr(self.evaluation_engine, "trajectory_verifier", None),
                tracer=self.tracer,
            )
        )
        self.skill_synthesizer = (
            skill_synthesizer
            if skill_synthesizer is not None
            else SkillSynthesizer(
                validator=self.sandbox_validator,
                artifact_manager=self.artifact_manager,
                tracer=self.tracer,
            )
        )
        self.dynamic_skill_registry = (
            dynamic_skill_registry
            if dynamic_skill_registry is not None
            else DynamicSkillRegistry(
                executor=self.sandbox_executor,
                tool_registry=self.tool_executor.registry if (self.tool_executor and hasattr(self.tool_executor, "registry")) else None,
                tracer=self.tracer,
            )
        )
        if hasattr(self.skill_registry, "set_dynamic_registry"):
            self.skill_registry.set_dynamic_registry(self.dynamic_skill_registry)

        if self.checkpoint_manager is not None and getattr(self.checkpoint_manager, "dynamic_skill_registry", None) is None:
            self.checkpoint_manager.dynamic_skill_registry = self.dynamic_skill_registry

        # M27 Causal Fault Diagnosis, Remediation Planning & Self-Healing Orchestration
        self.causal_fault_analyzer = (
            causal_fault_analyzer
            if causal_fault_analyzer is not None
            else CausalFaultAnalyzer(tracer=self.tracer)
        )
        self.remediation_planner = (
            remediation_planner
            if remediation_planner is not None
            else RemediationPlanner()
        )
        self.self_healing_orchestrator = (
            self_healing_orchestrator
            if self_healing_orchestrator is not None
            else SelfHealingOrchestrator(
                analyzer=self.causal_fault_analyzer,
                planner=self.remediation_planner,
                dynamic_skill_registry=self.dynamic_skill_registry,
                skill_synthesizer=self.skill_synthesizer,
                skill_verification_harness=self.verification_harness,
                clarification_gateway=self.clarification_gateway,
                streaming_gateway=self.streaming_gateway,
                tracer=self.tracer,
            )
        )
        if hasattr(self.campaign_engine, "self_healing_orchestrator") and self.campaign_engine.self_healing_orchestrator is None:
            self.campaign_engine.self_healing_orchestrator = self.self_healing_orchestrator

        if self.checkpoint_manager is not None and getattr(self.checkpoint_manager, "self_healing_orchestrator", None) is None:
            self.checkpoint_manager.self_healing_orchestrator = self.self_healing_orchestrator

        # M28 Epistemic Knowledge Graph, Experience Distillation & Semantic Query Engine
        self.epistemic_graph = (
            epistemic_graph
            if epistemic_graph is not None
            else EpistemicKnowledgeGraph()
        )
        self.experience_distiller = (
            experience_distiller
            if experience_distiller is not None
            else ExperienceDistiller(knowledge_graph=self.epistemic_graph)
        )
        self.epistemic_query_engine = (
            epistemic_query_engine
            if epistemic_query_engine is not None
            else EpistemicQueryEngine(knowledge_graph=self.epistemic_graph)
        )

        if self.checkpoint_manager is not None and getattr(self.checkpoint_manager, "epistemic_graph", None) is None:
            self.checkpoint_manager.epistemic_graph = self.epistemic_graph

        # M29 Release & Preflight Validator
        self.release_validator = (
            release_validator
            if release_validator is not None
            else ReleaseValidator()
        )

        # M30 Durable Personal State Store
        self.durable_state_store = (
            durable_state_store
            if durable_state_store is not None
            else DurablePersonalStateStore()
        )

        # M31 Advanced Multi-Source Retrieval / RAG Pipeline
        self.retrieval_pipeline = (
            retrieval_pipeline
            if retrieval_pipeline is not None
            else AdvancedRetrievalPipeline(
                durable_state_store=self.durable_state_store,
                epistemic_graph=self.epistemic_graph,
                artifact_manager=self.artifact_manager,
            )
        )

        # M32 Context & Personalization Engine
        self.context_engine = (
            context_engine
            if context_engine is not None
            else ContextPersonalizationEngine()
        )

        # M34 Tool & Action Ecosystem Registry
        self.tool_ecosystem = (
            tool_ecosystem
            if tool_ecosystem is not None
            else ToolEcosystemRegistry(policy_engine=self.policy)
        )

        # M33 Structured Planning Engine
        self.structured_planner = (
            structured_planner
            if structured_planner is not None
            else StructuredPlanningEngine(
                default_tool_executor=self.tool_ecosystem,
                policy_engine=self.policy,
            )
        )

        # M35 Proactive Assistance Engine
        self.proactive_engine = (
            proactive_engine
            if proactive_engine is not None
            else ProactiveAssistanceEngine(policy_engine=self.policy)
        )

        # M36 Experience & Learning Loop Engine
        self.learning_engine = (
            learning_engine
            if learning_engine is not None
            else ExperienceLearningEngine(
                durable_state_store=self.durable_state_store,
                epistemic_graph=self.epistemic_graph,
            )
        )

        # M37 Multimodal Foundation Processor
        self.multimodal_processor = (
            multimodal_processor
            if multimodal_processor is not None
            else MultimodalProcessor()
        )

        # M38 Device & Environment Integration Engine
        self.device_engine = (
            device_engine
            if device_engine is not None
            else DeviceIntegrationEngine(policy_engine=self.policy)
        )

        # M39 Cross-Device AURA State Sync Engine
        self.cross_device_sync = (
            cross_device_sync
            if cross_device_sync is not None
            else CrossDeviceSyncEngine(
                durable_state_store=self.durable_state_store,
            )
        )

        # M40 Integrated Personal Intelligence Engine
        self.integrated_intelligence_engine = (
            integrated_intelligence_engine
            if integrated_intelligence_engine is not None
            else IntegratedPersonalIntelligenceEngine(
                durable_state_store=self.durable_state_store,
                retrieval_pipeline=self.retrieval_pipeline,
                context_engine=self.context_engine,
                structured_planner=self.structured_planner,
                tool_ecosystem=self.tool_ecosystem,
                proactive_engine=self.proactive_engine,
                learning_engine=self.learning_engine,
                multimodal_processor=self.multimodal_processor,
                device_engine=self.device_engine,
                cross_device_sync=self.cross_device_sync,
                policy_engine=self.policy,
                model_router=self.model_router,
            )
        )

    def execute(
        self,
        task: str | ExecutionPlan | AgentPlan | Goal | AURARequest | TeamDefinition,
        mode: ExecutionMode | str | None = None,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trigger_id: str | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
    ) -> WorkflowResult | AutonomousAgentResult | GoalEvaluationResult | TeamExecutionResult:
        """Execute a task in the specified ExecutionMode (STANDARD_WORKFLOW, AUTONOMOUS_AGENT, GOAL_DRIVEN, MULTI_AGENT_TEAM)."""
        effective_mode = self.default_mode
        if mode is not None:
            effective_mode = ExecutionMode(mode) if isinstance(mode, str) else mode
        elif isinstance(task, TeamDefinition):
            effective_mode = ExecutionMode.MULTI_AGENT_TEAM
        elif isinstance(task, AgentPlan):
            effective_mode = ExecutionMode.AUTONOMOUS_AGENT
        elif isinstance(task, Goal):
            effective_mode = ExecutionMode.GOAL_DRIVEN
        elif isinstance(task, ExecutionPlan):
            effective_mode = ExecutionMode.STANDARD_WORKFLOW

        if effective_mode == ExecutionMode.STANDARD_WORKFLOW:
            return self.execute_task(
                task=task,
                task_id=task_id,
                task_requirements=task_requirements,
                timeout=timeout,
                metadata=metadata,
            )
        elif effective_mode == ExecutionMode.AUTONOMOUS_AGENT:
            return self.execute_autonomous(
                task=task,
                task_id=task_id,
                task_requirements=task_requirements,
                metadata=metadata,
            )
        elif effective_mode == ExecutionMode.GOAL_DRIVEN:
            return self.execute_goal(
                goal=task,
                trigger_id=trigger_id,
                context=context,
                metadata=metadata,
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
        elif effective_mode == ExecutionMode.MULTI_AGENT_TEAM:
            if isinstance(task, TeamDefinition):
                return self.execute_team(task=task.description or task.name, team=task, session_id="default", metadata=metadata)
            return self.execute_team(task=str(task), session_id="default", metadata=metadata)
        else:
            raise ValueError(f"Unsupported execution mode: {effective_mode}")

    def execute_autonomous(
        self,
        task: str | AgentPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutonomousAgentResult:
        """Execute an autonomous task or plan through AutonomousAgentExecutor."""
        actual_task_id = task_id
        meta = dict(metadata) if metadata is not None else {}

        if isinstance(task, AURARequest):
            actual_task_id = actual_task_id or str(task.request_id)
            task_desc = task.user_input
            meta.update(task.metadata)
            return self.autonomous_executor.run(
                task=task_desc,
                task_id=actual_task_id,
                task_requirements=task_requirements,
                metadata=meta,
            )
        elif isinstance(task, str):
            if not task.strip():
                raise ValueError("Task description cannot be empty.")
            return self.autonomous_executor.run(
                task=task.strip(),
                task_id=actual_task_id,
                task_requirements=task_requirements,
                metadata=meta,
            )
        elif isinstance(task, AgentPlan):
            return self.autonomous_executor.execute_plan(
                plan=task,
                task_id=actual_task_id,
            )
        else:
            raise TypeError("task must be a string, AgentPlan, or AURARequest for autonomous execution.")

    def execute_goal(
        self,
        goal: str | Goal | AURARequest,
        trigger_id: str | None = None,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
    ) -> GoalEvaluationResult:
        """Execute or evaluate a goal through GoalEngine."""
        if isinstance(goal, AURARequest):
            g = self.goal_engine.create_goal(
                title=goal.user_input,
                metadata=dict(goal.metadata),
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
            return self.goal_engine.evaluate_goal(
                goal_id=g.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        elif isinstance(goal, str):
            clean_gid = goal.strip()
            if not clean_gid:
                raise ValueError("Goal title cannot be empty.")
            if self.goal_store.exists(clean_gid):
                return self.goal_engine.evaluate_goal(
                    goal_id=clean_gid,
                    trigger_id=trigger_id,
                    context=context,
                )
            g = self.goal_engine.create_goal(
                title=clean_gid,
                metadata=dict(metadata or {}),
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
            return self.goal_engine.evaluate_goal(
                goal_id=g.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        elif isinstance(goal, Goal):
            if not self.goal_store.exists(goal.goal_id):
                self.goal_store.create(goal)
            return self.goal_engine.evaluate_goal(
                goal_id=goal.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        else:
            raise TypeError("goal must be a string, Goal, or AURARequest for goal-driven execution.")

    def execute_task(
        self,
        task: str | ExecutionPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a task through the standard workflow executor pipeline."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        actual_task_id = task_id
        meta = dict(metadata) if metadata is not None else {}

        if isinstance(task, AURARequest):
            actual_task_id = actual_task_id or str(task.request_id)
            task_desc = task.user_input
            meta.update(task.metadata)
        elif isinstance(task, str):
            if not task.strip():
                raise ValueError("Task description cannot be empty.")
            task_desc = task.strip()
        elif isinstance(task, ExecutionPlan):
            return self.workflow_executor.execute(
                plan=task,
                task_id=actual_task_id,
                timeout=effective_timeout,
            )
        else:
            raise TypeError("task must be a string, ExecutionPlan, or AURARequest.")

        actual_task_id = actual_task_id or str(uuid4())

        # 1. Generate execution plan via TaskPlanner
        try:
            plan = self.planner.plan(
                task=task_desc,
                task_requirements=task_requirements,
                metadata={"task": task_desc, **meta},
            )
        except Exception as e:
            logger.warning("Agentic task planning failed for '%s': %s", task_desc, e)
            return WorkflowResult(
                success=False,
                plan_id="",
                task_id=actual_task_id,
                error=f"Task planning failed: {str(e)}",
                metadata={"planning_error": True, "task": task_desc},
            )

        # 2. Execute plan via WorkflowExecutor
        prev_task_desc = self.workflow_executor.task_description
        try:
            self.workflow_executor.task_description = task_desc
            return self.workflow_executor.execute(
                plan=plan,
                task_id=actual_task_id,
                timeout=effective_timeout,
            )
        finally:
            self.workflow_executor.task_description = prev_task_desc

    def resume_task(
        self,
        task_id: str,
        plan: ExecutionPlan | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Resume an interrupted or paused task through WorkflowExecutor."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        return self.workflow_executor.resume(
            task_id=task_id,
            plan=plan,
            timeout=effective_timeout,
        )

    def run_request(
        self,
        request: AURARequest,
        timeout: float | None = None,
    ) -> AURAResponse:
        """Execute an AURARequest through the agentic runtime and return a standard AURAResponse."""
        if not isinstance(request, AURARequest):
            raise TypeError("request must be an instance of AURARequest.")

        result = self.execute_task(task=request, timeout=timeout)
        content = (
            str(result.final_output)
            if result.success and result.final_output is not None
            else (result.error or "")
        )
        resp_metadata = {
            "agentic": "true",
            "success": str(result.success).lower(),
            "plan_id": str(result.plan_id),
            "task_id": str(result.task_id or request.request_id),
            "executed_steps": str(result.executed_steps),
        }
        for k, v in result.metadata.items():
            resp_metadata[str(k)] = str(v)

        return AURAResponse(
            request_id=request.request_id,
            content=content,
            metadata=resp_metadata,
        )

    def run(
        self,
        task: str | ExecutionPlan | AgentPlan | Goal | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        mode: ExecutionMode | str | None = None,
    ) -> WorkflowResult | AutonomousAgentResult | GoalEvaluationResult:
        """Unified entry point to execute tasks across all modes."""
        return self.execute(
            task=task,
            mode=mode,
            task_id=task_id,
            task_requirements=task_requirements,
            timeout=timeout,
            metadata=metadata,
        )

    # ---------------------------------------------------------
    # Memory Lifecycle & Calibration Helpers (M15)
    # ---------------------------------------------------------
    def run_memory_lifecycle_pass(
        self,
        current_time: float | None = None,
    ) -> dict[str, Any]:
        """Execute a memory maintenance pass across all memory tiers."""
        return self.memory_manager.run_lifecycle_pass(current_time=current_time)

    def compact_memory(
        self,
        tier: Any = None,
        namespace: str | None = None,
        current_time: float | None = None,
    ) -> Any:
        """Execute compaction pass on a specific memory tier or namespace."""
        from core.memory_types import MemoryTier
        effective_tier = tier if tier is not None else MemoryTier.SEMANTIC
        return self.memory_manager.compact_memory(
            tier=effective_tier,
            namespace=namespace,
            current_time=current_time,
        )

    def get_heuristic_calibrator(self) -> Any:
        """Return the runtime's heuristic calibrator instance."""
        return self.calibrator

    # ---------------------------------------------------------
    # Scheduling, Resource Governance & Event Dispatch (M17)
    # ---------------------------------------------------------
    def get_budget_manager(self) -> ResourceBudgetManager:
        """Return the runtime's resource budget manager."""
        return self.budget_manager

    def get_lock_manager(self) -> SharedResourceLockManager:
        """Return the runtime's shared resource lock manager."""
        return self.lock_manager

    def get_scheduler(self) -> MultiGoalScheduler:
        """Return the runtime's multi-goal priority scheduler."""
        return self.scheduler

    def get_event_dispatcher(self) -> ProactiveEventDispatcher:
        """Return the runtime's proactive event dispatcher."""
        return self.event_dispatcher

    def get_clarification_gateway(self) -> ClarificationGateway:
        """Return the runtime's interactive clarification gateway."""
        return self.clarification_gateway

    def publish_event(self, event: ProactiveEvent) -> int:
        """Publish a proactive event to trigger subscribed goals."""
        return self.event_dispatcher.publish_event(event)

    def step_scheduled_goals(self, max_batch_size: int = 4) -> list[GoalEvaluationResult]:
        """Step the multi-goal scheduler to evaluate the next batch of queued goals."""
        # Enqueue any active goals from the store into the scheduler
        for g in self.goal_engine.list_goals(status=GoalStatus.ACTIVE):
            self.scheduler.schedule_goal(g.goal_id, priority=g.priority)
        return self.scheduler.step_next_batch(self.goal_engine, max_batch_size=max_batch_size)

    # ---------------------------------------------------------
    # Runtime Supervision & Checkpoint Management (M18)
    # ---------------------------------------------------------
    def get_checkpoint_manager(self) -> RuntimeCheckpointManager:
        """Return the runtime's session checkpoint manager."""
        return self.checkpoint_manager

    def get_supervisor(self) -> AutonomousSupervisor:
        """Return the runtime's autonomous supervisor daemon instance."""
        return self.supervisor

    def start_daemon(self, auto_recover: bool | None = None) -> bool:
        """Start the background supervisor daemon."""
        return self.supervisor.start(auto_recover=auto_recover)

    def stop_daemon(self, timeout: float | None = None) -> bool:
        """Gracefully stop the background supervisor daemon."""
        return self.supervisor.stop(timeout=timeout)

    def is_daemon_running(self) -> bool:
        """Check if the supervisor daemon is actively running."""
        return self.supervisor.is_running()

    @property
    def daemon_status(self) -> DaemonStatus:
        """Return the current lifecycle status of the supervisor daemon."""
        return self.supervisor.status

    def get_supervisor_telemetry(self) -> SupervisorTelemetry:
        """Return an aggregated telemetry and health diagnostics snapshot."""
        return self.supervisor.get_telemetry()

    def create_checkpoint(
        self,
        checkpoint_id: str | None = None,
        is_clean_shutdown: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointMetadata:
        """Create and persist an atomic runtime session checkpoint."""
        return self.checkpoint_manager.save_checkpoint(
            checkpoint_id=checkpoint_id,
            is_clean_shutdown=is_clean_shutdown,
            metadata=metadata,
        )

    def restore_checkpoint(
        self,
        checkpoint_path: str | Path | None = None,
    ) -> CheckpointMetadata | None:
        """Restore runtime state from a specific checkpoint or the latest valid checkpoint."""
        if checkpoint_path is not None:
            return self.checkpoint_manager.restore_from_file(checkpoint_path)
        return self.checkpoint_manager.restore_latest_checkpoint()

    # ---------------------------------------------------------
    # Multi-Session, Real-Time Streaming & Operator Bridge (M19)
    # ---------------------------------------------------------
    def get_session_manager(self) -> SessionManager:
        """Return the multi-session manager instance."""
        return self.session_manager

    def get_streaming_gateway(self) -> StreamingGateway:
        """Return the real-time event streaming gateway."""
        return self.streaming_gateway

    def get_operator_bridge(self) -> OperatorBridge:
        """Return the human-in-the-loop operator bridge."""
        return self.operator_bridge

    def create_session(
        self,
        session_id: str | None = None,
        user_id: str = "default_user",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> SessionContext:
        """Create a new isolated session context."""
        return self.session_manager.create_session(
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    def get_session(self, session_id: str) -> SessionContext | None:
        """Retrieve an existing session context by ID."""
        return self.session_manager.get_session(session_id=session_id)

    def close_session(self, session_id: str, reason: str = "normal") -> bool:
        """Close an active session and emit SESSION_CLOSED event."""
        res = self.session_manager.close_session(session_id=session_id, reason=reason)
        if res:
            self.streaming_gateway.create_and_publish(
                session_id=session_id,
                event_type=StreamEventType.SESSION_CLOSED,
                data={"reason": reason},
            )
        return res

    def submit_session_goal(
        self,
        title: str,
        session_id: str = "default",
        priority: Any = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: tuple[str, ...] | list[str] = (),
    ) -> Goal:
        """Submit a goal bound to a specific session and register it with the scheduler."""
        from core.goal import GoalPriority
        eff_priority = priority if priority is not None else GoalPriority.MEDIUM
        ctx = self.session_manager.get_or_create_session(session_id=session_id)

        goal = self.goal_engine.create_goal(
            title=title,
            priority=eff_priority,
            metadata=metadata,
            parent_goal_id=parent_goal_id,
            depends_on_goal_ids=depends_on_goal_ids,
        )
        self.session_manager.bind_goal(session_id=session_id, goal_id=goal.goal_id)
        self.scheduler.schedule_goal(goal.goal_id, priority=goal.priority)

        self.streaming_gateway.create_and_publish(
            session_id=session_id,
            event_type=StreamEventType.GOAL_UPDATED,
            data={"goal_id": goal.goal_id, "title": goal.title, "status": goal.status.value, "priority": str(goal.priority)},
            goal_id=goal.goal_id,
        )
        return goal

    def submit_team_goal(
        self,
        title: str,
        description: str = "",
        team_id: str | None = None,
        role_id: str | None = None,
        topology: Any = None,
        success_criteria: list[str] | tuple[str, ...] = (),
        constraints: list[str] | tuple[str, ...] = (),
        priority: Any = None,
        metadata: dict[str, Any] | None = None,
        auto_activate: bool = True,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: Sequence[str] = (),
    ) -> Goal:
        """Create and schedule a multi-agent team bound goal (M22)."""
        from core.goal import GoalPriority
        from core.team_types import TeamTopology
        eff_pri = priority if isinstance(priority, GoalPriority) else (GoalPriority(priority) if isinstance(priority, str) else GoalPriority.MEDIUM)
        eff_topo = topology.value if isinstance(topology, TeamTopology) else (str(topology) if topology else None)
        goal = self.goal_engine.create_goal(
            title=title,
            description=description,
            success_criteria=success_criteria,
            constraints=constraints,
            priority=eff_pri,
            auto_activate=auto_activate,
            metadata=metadata,
            parent_goal_id=parent_goal_id,
            depends_on_goal_ids=tuple(depends_on_goal_ids),
            assigned_team_id=team_id,
            assigned_role_id=role_id,
            execution_topology=eff_topo,
        )
        self.scheduler.schedule_goal(
            goal.goal_id,
            priority=goal.priority,
            assigned_team_id=goal.assigned_team_id,
            assigned_role_id=goal.assigned_role_id,
            execution_topology=goal.execution_topology,
        )
        return goal

    def approve_action(
        self,
        approval_id: str,
        session_id: str = "default",
        operator_id: str = "operator",
        rationale: str = "",
    ) -> OperatorResolution:
        """Submit an operator approval action."""
        action = OperatorAction(
            session_id=session_id,
            request_id=approval_id,
            action_type=OperatorActionType.APPROVE,
            operator_id=operator_id,
            decision_rationale=rationale,
        )
        return self.operator_bridge.submit_action(action)

    def answer_clarification(
        self,
        clarification_id: str,
        response_data: Any,
        session_id: str = "default",
        operator_id: str = "operator",
    ) -> OperatorResolution:
        """Submit an operator response to a pending clarification request."""
        action = OperatorAction(
            session_id=session_id,
            request_id=clarification_id,
            action_type=OperatorActionType.CLARIFY,
            operator_id=operator_id,
            clarification_payload=response_data if isinstance(response_data, dict) else {"response": response_data},
        )
        return self.operator_bridge.submit_action(action)

    def publish_stream_event(self, event: StreamEvent) -> int:
        """Publish an event to active stream subscribers."""
        return self.streaming_gateway.publish(event)

    def subscribe_stream(
        self,
        subscriber_id: str | None = None,
        session_id: str | None = None,
        event_types: set[StreamEventType] | None = None,
        last_event_id: str | None = None,
    ) -> tuple[str, Any]:
        """Subscribe to real-time events on the streaming gateway."""
        return self.streaming_gateway.subscribe(
            subscriber_id=subscriber_id,
            session_id=session_id,
            event_types=event_types,
            last_event_id=last_event_id,
        )

    def send_message_stream(
        self,
        message: str,
        session_id: str = "default",
        user_id: str = "default_user",
        mode: ExecutionMode | str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Execute a message or task in the context of an isolated session, streaming progress events and updating session history."""
        ctx = self.session_manager.get_or_create_session(session_id=session_id, user_id=user_id)

        # 1. Emit STEP_STARTED
        step_event = self.streaming_gateway.create_and_publish(
            session_id=session_id,
            event_type=StreamEventType.STEP_STARTED,
            data={"user_input": message, "mode": str(mode or self.default_mode)},
        )
        yield step_event

        try:
            eff_mode = mode if mode is not None else self.default_mode
            result = self.execute(
                task=message,
                mode=eff_mode,
                metadata=metadata,
            )

            if isinstance(result, WorkflowResult):
                output_text = str(result.final_output or (result.error if not result.success else "Task completed successfully."))
                tool_name = None
                tool_res = None
            elif isinstance(result, AutonomousAgentResult):
                output_text = str(result.final_answer or (result.error if not result.success else "Goal accomplished."))
                tool_name = None
                tool_res = None
            elif isinstance(result, GoalEvaluationResult):
                output_text = f"Goal {result.goal_id} evaluated with status {result.status}."
                tool_name = None
                tool_res = None
            else:
                output_text = str(result)
                tool_name = None
                tool_res = None

            # 2. Emit TOKEN_CHUNK
            token_event = self.streaming_gateway.create_and_publish(
                session_id=session_id,
                event_type=StreamEventType.TOKEN_CHUNK,
                data={"chunk": output_text},
            )
            yield token_event

            # 3. Add to session history
            self.session_manager.add_turn(
                session_id=session_id,
                user_input=message,
                assistant_output=output_text,
                tool_name=tool_name,
                tool_result=tool_res,
            )

            # 4. Emit STEP_COMPLETED
            completed_event = self.streaming_gateway.create_and_publish(
                session_id=session_id,
                event_type=StreamEventType.STEP_COMPLETED,
                data={"result": output_text, "success": True},
            )
            yield completed_event

        except Exception as e:
            error_event = self.streaming_gateway.create_and_publish(
                session_id=session_id,
                event_type=StreamEventType.ERROR,
                data={"error": str(e)},
            )
            yield error_event
            raise

    # ---------------------------------------------------------
    # Model Provider Health & Resilient Routing (M20)
    # ---------------------------------------------------------
    def get_health_tracker(self) -> ProviderHealthTracker:
        """Return the model provider health tracker instance."""
        return self.health_tracker

    def get_resilient_router(self) -> ResilientModelRouter | None:
        """Return the resilient model router instance if configured."""
        return self.resilient_router

    def get_provider_health_telemetry(self) -> dict[str, Any]:
        """Retrieve sanitized health telemetry for all tracked model providers."""
        return self.health_tracker.get_all_telemetry()

    # ---------------------------------------------------------
    # Multi-Agent Team Collaboration & Delegation (M21)
    # ---------------------------------------------------------
    def get_role_registry(self) -> RoleRegistry:
        """Return the active agent role registry."""
        return self.role_registry

    def get_agent_message_bus(self) -> AgentMessageBus:
        """Return the inter-agent message bus."""
        return self.message_bus

    def get_consensus_engine(self) -> ConsensusEngine:
        """Return the multi-agent consensus and artifact synthesis engine."""
        return self.consensus_engine

    def get_team_orchestrator(self) -> TeamOrchestrator:
        """Return the multi-agent team orchestrator."""
        return self.team_orchestrator

    def execute_team(
        self,
        task: str,
        team: TeamDefinition | None = None,
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> TeamExecutionResult:
        """Execute a multi-agent collaborative task across a team topology."""
        return self.team_orchestrator.execute_team(
            task=task,
            team=team,
            session_id=session_id,
            metadata=metadata,
        )

    def delegate_task(
        self,
        contract: DelegationContract,
        session_id: str = "default",
        team_id: str = "default_team",
    ) -> DelegationResult:
        """Execute a formal task delegation from one role to another."""
        return self.team_orchestrator.delegate(
            contract=contract,
            session_id=session_id,
            team_id=team_id,
        )

    def submit_team_goal(
        self,
        title: str,
        description: str,
        team: TeamDefinition | None = None,
        topology: TeamTopology | str | None = None,
        priority: GoalPriority | None = None,
        success_criteria: tuple[str, ...] | list[str] = (),
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        """Submit a goal explicitly bound to a multi-agent team or topology (M22)."""
        from core.goal_team_binding import sanitize_binding_metadata
        topo = None
        team_id = None
        clean_meta = sanitize_binding_metadata(metadata or {})
        if team is not None:
            team_id = team.team_id
            topo = team.topology
            if hasattr(team, "members") and team.members:
                clean_meta["team_members"] = [{"role_id": m.role_id, "is_lead": m.is_lead} for m in team.members]
            if hasattr(team, "name") and team.name:
                clean_meta["team_name"] = team.name
            if hasattr(team, "metadata") and team.metadata:
                sanitized_team_meta = sanitize_binding_metadata(team.metadata)
                clean_meta = {**sanitized_team_meta, **clean_meta}
        elif topology is not None:
            topo = TeamTopology(topology) if isinstance(topology, str) else topology

        if session_id:
            clean_meta["session_id"] = str(session_id).strip()

        goal = self.goal_engine.create_goal(
            title=title,
            description=description,
            priority=priority or GoalPriority.MEDIUM,
            success_criteria=tuple(success_criteria),
            assigned_team_id=team_id,
            execution_topology=topo,
            metadata=clean_meta,
        )
        if self.scheduler is not None:
            self.scheduler.schedule_goal(
                goal_id=goal.goal_id,
                priority=goal.priority,
                assigned_team_id=goal.assigned_team_id,
                assigned_role_id=goal.assigned_role_id,
                execution_topology=topo.value if hasattr(topo, "value") else (str(topo) if topo is not None else None),
                metadata=clean_meta,
            )
        return goal

    def get_evaluation_engine(self) -> EvaluationEngine:
        """Return the EvaluationEngine instance (M23)."""
        return self.evaluation_engine

    def evaluate_execution(
        self,
        target: Any,
        target_id: str | None = None,
        target_type: str | None = None,
        expected_criteria: tuple[str, ...] | list[str] = (),
        context: dict[str, Any] | None = None,
    ) -> EvaluationReport:
        """Evaluate an execution result, trajectory, or goal using the EvaluationEngine (M23)."""
        return self.evaluation_engine.evaluate(
            target=target,
            target_id=target_id,
            target_type=target_type,
            expected_criteria=expected_criteria,
            context=context,
        )

    def run_benchmark(
        self,
        suite: Any | None = None,
        scenario_ids: list[str] | tuple[str, ...] | None = None,
        stop_on_failure: bool = False,
    ) -> BenchmarkRunSummary:
        """Run a benchmark suite against this runtime instance (M23)."""
        from evaluation.benchmark_suite import BenchmarkSuite
        eff_suite = suite or BenchmarkSuite(evaluation_engine=self.evaluation_engine)
        return eff_suite.run(
            runtime_or_aura=self,
            scenario_ids=scenario_ids,
            stop_on_failure=stop_on_failure,
        )

    # ---------------------------------------------------------
    # M24 Distributed Tracing, Artifacts & Adaptive Optimizer
    # ---------------------------------------------------------
    def get_tracer(self) -> Tracer:
        """Return the active distributed Tracer (M24)."""
        return self.tracer

    def get_trace(self, trace_id: str) -> list[SpanRecord]:
        """Retrieve all spans for a specific trace_id (M24)."""
        return self.trace_exporter.get_spans(trace_id)

    def get_causal_graph(self, trace_id: str) -> CausalExecutionGraph:
        """Construct a CausalExecutionGraph for a trace (M24)."""
        spans = self.get_trace(trace_id)
        return CausalExecutionGraph(spans)

    def get_artifact_manager(self) -> ArtifactManager:
        """Return the active ArtifactManager instance (M24)."""
        return self.artifact_manager

    def create_artifact(
        self,
        name: str,
        content: Any,
        artifact_type: ArtifactType | str = ArtifactType.DOCUMENT,
        session_id: str | None = None,
        creator_role_id: str | None = None,
        producer_goal_id: str | None = None,
        producer_task_id: str | None = None,
        parent_artifact_ids: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        taint_status: bool | None = None,
    ) -> Artifact:
        """Store a new version 1 artifact deliverable (M24)."""
        return self.artifact_manager.store_artifact(
            name=name,
            content=content,
            artifact_type=artifact_type,
            session_id=session_id,
            creator_role_id=creator_role_id,
            producer_goal_id=producer_goal_id,
            producer_task_id=producer_task_id,
            parent_artifact_ids=parent_artifact_ids,
            metadata=metadata,
            taint_status=taint_status,
        )

    def get_artifact(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        """Retrieve an artifact manifest by ID and optional version (M24)."""
        return self.artifact_manager.get_artifact(artifact_id=artifact_id, version=version)

    def get_artifact_content(self, artifact_id: str, version: int | None = None, decode_text: bool = True) -> Any:
        """Retrieve content of an artifact by ID (M24)."""
        return self.artifact_manager.get_artifact_content(
            artifact_id=artifact_id,
            version=version,
            decode_text=decode_text,
        )

    def list_artifacts(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        artifact_type: ArtifactType | str | None = None,
    ) -> list[Artifact]:
        """List registered artifacts (M24)."""
        return self.artifact_manager.list_artifacts(
            session_id=session_id,
            goal_id=goal_id,
            artifact_type=artifact_type,
        )

    def get_artifact_lineage(self, artifact_id: str) -> ArtifactLineage:
        """Get derivation lineage DAG for an artifact (M24)."""
        return self.artifact_manager.get_lineage(artifact_id)

    def get_adaptive_optimizer(self) -> AdaptivePolicyOptimizer:
        """Return the AdaptivePolicyOptimizer instance (M24)."""
        return self.adaptive_optimizer

    def get_feedback_bridge(self) -> FeedbackBridge:
        """Return the FeedbackBridge instance (M24)."""
        return self.feedback_bridge

    def optimize_from_evaluation(
        self,
        report: EvaluationReport,
        goal: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[OptimizationEvent]:
        """Perform closed-loop adaptive policy optimization from an evaluation report (M24)."""
        return self.feedback_bridge.process_evaluation(
            report=report,
            goal=goal,
            context=context,
        )

    def get_optimization_history(self) -> list[OptimizationEvent]:
        """Retrieve all recorded optimization events (M24)."""
        return self.adaptive_optimizer.history.list_events()

    # ------------------------------------------------------------------
    # M25 Autonomous Mission Campaign Orchestration & Distributed Sagas
    # ------------------------------------------------------------------
    def get_campaign_engine(self) -> CampaignEngine:
        """Return the master CampaignEngine instance (M25)."""
        return self.campaign_engine

    def submit_campaign(self, definition: CampaignDefinition) -> CampaignDefinition:
        """Submit and validate a new multi-phase mission campaign (M25)."""
        return self.campaign_engine.submit_campaign(definition)

    def execute_campaign(
        self,
        campaign_id: str,
        session_id: str | None = None,
        max_iterations: int = 50,
    ) -> CampaignExecutionResult:
        """Execute a multi-phase mission campaign DAG to completion (M25)."""
        return self.campaign_engine.execute_campaign(
            campaign_id=campaign_id,
            session_id=session_id,
            max_iterations=max_iterations,
        )

    def get_campaign(self, campaign_id: str) -> CampaignDefinition | None:
        """Retrieve campaign definition by ID (M25)."""
        return self.campaign_engine.get_campaign(campaign_id)

    def get_campaign_status(self, campaign_id: str) -> dict[str, Any]:
        """Query real-time status and phase progress of a campaign (M25)."""
        return self.campaign_engine.get_campaign_status(campaign_id)

    def list_campaigns(self) -> list[dict[str, Any]]:
        """List all registered campaigns (M25)."""
        return self.campaign_engine.list_campaigns()

    def cancel_campaign(self, campaign_id: str, reason: str = "User cancelled") -> bool:
        """Cancel a running or scheduled campaign (M25)."""
        return self.campaign_engine.cancel_campaign(campaign_id, reason=reason)

    def rollback_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        """Manually trigger a full saga rollback of all executed steps in a campaign (M25)."""
        return self.campaign_engine.rollback_campaign(campaign_id)

    # ------------------------------------------------------------------
    # M26 Dynamic Skill Synthesis, Verification & Registry Methods
    # ------------------------------------------------------------------
    def get_dynamic_skill_registry(self) -> DynamicSkillRegistry:
        return self.dynamic_skill_registry

    def get_skill_synthesizer(self) -> SkillSynthesizer:
        return self.skill_synthesizer

    def get_verification_harness(self) -> SkillVerificationHarness:
        return self.verification_harness

    def synthesize_skill(
        self,
        name: str,
        description: str,
        source_code: str,
        entrypoint_function: str = "execute",
        test_vectors: Sequence[TestVector | dict[str, Any]] = (),
        required_capabilities: Sequence[str] = (),
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        author_role_id: str = "coder",
        originating_goal_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        verify_after_synthesis: bool = True,
    ) -> SynthesizedSkill:
        """Synthesize a new programmatic Python tool and optionally verify it (M26)."""
        skill = self.skill_synthesizer.synthesize_tool(
            name=name,
            description=description,
            source_code=source_code,
            entrypoint_function=entrypoint_function,
            test_vectors=test_vectors,
            required_capabilities=required_capabilities,
            input_schema=input_schema,
            output_schema=output_schema,
            author_role_id=author_role_id,
            originating_goal_id=originating_goal_id,
            metadata=metadata,
        )
        if verify_after_synthesis:
            self.verification_harness.verify_skill(skill)
        return skill

    def synthesize_composite_skill(
        self,
        name: str,
        description: str,
        steps: Sequence[SkillStep | dict[str, Any]],
        author_role_id: str = "architect",
        originating_goal_id: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompositeSkill:
        """Synthesize a declarative composite skill pipeline (M26)."""
        return self.skill_synthesizer.synthesize_composite_skill(
            name=name,
            description=description,
            steps=steps,
            author_role_id=author_role_id,
            originating_goal_id=originating_goal_id,
            input_schema=input_schema,
            output_schema=output_schema,
            metadata=metadata,
        )

    def verify_skill(
        self,
        skill: SynthesizedSkill,
        additional_test_vectors: tuple[TestVector, ...] = (),
    ) -> SkillVerificationReport:
        """Verify a synthesized skill against test vectors and trajectory invariants (M26)."""
        return self.verification_harness.verify_skill(skill, additional_test_vectors=additional_test_vectors)

    def register_dynamic_skill(
        self,
        skill: SynthesizedSkill,
        activate: bool = True,
        verify_first: bool = False,
    ) -> None:
        """Register a synthesized skill into the dynamic catalog and distill into knowledge graph (M26/M28)."""
        if verify_first and not skill.is_verified:
            self.verification_harness.verify_skill(skill)
        self.dynamic_skill_registry.register_skill(skill, activate=activate)
        if getattr(self, "experience_distiller", None) is not None:
            try:
                self.experience_distiller.distill_dynamic_skill(skill)
            except Exception as ex:
                logger.debug("AgenticRuntime: skill distillation skipped: %s", ex)

    def register_composite_skill(
        self,
        skill: CompositeSkill,
        activate: bool = True,
    ) -> None:
        """Register a composite skill pipeline into the dynamic catalog (M26)."""
        self.dynamic_skill_registry.register_composite_skill(skill, activate=activate)

    def execute_dynamic_skill(
        self,
        name: str,
        input_data: str,
        timeout: float | None = None,
    ) -> str:
        """Execute a dynamic skill inside the isolated sandbox (M26)."""
        return self.dynamic_skill_registry.execute_skill(
            name=name,
            input_data=input_data,
            timeout=timeout,
        )

    # ---------------------------------------------------------
    # M27 Causal Fault Diagnosis & Autonomous Self-Healing
    # ---------------------------------------------------------
    def diagnose_failure(
        self,
        campaign_id: str,
        phase_id: str,
        goal_id: str,
        error_message: str,
        error_traceback: str = "",
        causal_graph: Any | None = None,
        failing_input: str = "",
        affected_artifact_ids: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> FaultDiagnosticReport:
        """Diagnose a campaign phase failure using causal trace analysis (M27)."""
        return self.causal_fault_analyzer.analyze(
            campaign_id=campaign_id,
            phase_id=phase_id,
            goal_id=goal_id,
            error_message=error_message,
            error_traceback=error_traceback,
            causal_graph=causal_graph,
            failing_input=failing_input,
            affected_artifact_ids=affected_artifact_ids,
            context=context,
        )

    def plan_remediation(
        self,
        fault_report: FaultDiagnosticReport,
        budget: HealingBudget | None = None,
        context: dict[str, Any] | None = None,
    ) -> RemediationPlan:
        """Synthesize a bounded multi-tier remediation plan for a diagnosed fault (M27)."""
        return self.remediation_planner.plan(
            fault_report=fault_report,
            budget=budget,
            context=context,
        )

    def heal_campaign_phase(
        self,
        campaign_id: str,
        phase_id: str,
        goal_id: str,
        error_message: str,
        error_traceback: str = "",
        causal_graph: Any | None = None,
        failing_input: str = "",
        affected_artifact_ids: list[str] | None = None,
        saga_coordinator: Any | None = None,
        context: dict[str, Any] | None = None,
        budget: HealingBudget | None = None,
    ) -> SelfHealingResult:
        """Execute a closed-loop diagnosis, remediation, and recovery cycle for a failed phase (M27)."""
        return self.self_healing_orchestrator.heal_campaign_phase(
            campaign_id=campaign_id,
            phase_id=phase_id,
            goal_id=goal_id,
            error_message=error_message,
            error_traceback=error_traceback,
            causal_graph=causal_graph,
            failing_input=failing_input,
            affected_artifact_ids=affected_artifact_ids,
            saga_coordinator=saga_coordinator,
            context=context,
            budget=budget,
        )

    def get_healing_history(self, campaign_id: str) -> list[SelfHealingResult]:
        """Retrieve self-healing attempt history for a mission campaign (M27)."""
        return self.self_healing_orchestrator.get_healing_history(campaign_id)

    def get_healing_attempt_count(self, campaign_id: str, phase_id: str) -> int:
        """Retrieve the number of healing attempts executed for a specific campaign phase (M27)."""
        return self.self_healing_orchestrator.get_attempt_count(campaign_id, phase_id)

    # ---------------------------------------------------------
    # M28 Epistemic Knowledge Graph & Semantic Queries
    # ---------------------------------------------------------
    def query_knowledge_graph(self, query: KnowledgeGraphQuery) -> list[KnowledgeEntity]:
        """Execute a multi-criteria search query over the Epistemic Knowledge Graph (M28)."""
        return self.epistemic_graph.query(query)

    def recommend_remediation(
        self,
        fault_category: str | FaultCategory,
        error_message: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve and rank proven remediation recipes from the knowledge graph (M28)."""
        return self.epistemic_query_engine.recommend_remediation(
            fault_category=fault_category,
            error_message=error_message,
            limit=limit,
        )

    def recommend_skills(
        self,
        task_description: str = "",
        required_capabilities: tuple[str, ...] = (),
        min_confidence: float = 0.5,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve and rank dynamic skills matching requested capabilities (M28)."""
        return self.epistemic_query_engine.recommend_skills(
            task_description=task_description,
            required_capabilities=required_capabilities,
            min_confidence=min_confidence,
            limit=limit,
        )

    def recommend_role_allocation(
        self,
        goal_title: str,
        required_capabilities: tuple[str, ...] = (),
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Recommend multi-agent roles with proven capability alignment (M28)."""
        return self.epistemic_query_engine.recommend_role_allocation(
            goal_title=goal_title,
            required_capabilities=required_capabilities,
            limit=limit,
        )

    def find_proven_goal_patterns(
        self,
        goal_domain: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve successful multi-phase mission execution patterns (M28)."""
        return self.epistemic_query_engine.find_proven_goal_patterns(
            goal_domain=goal_domain,
            limit=limit,
        )

    def query_knowledge_subgraph(
        self,
        root_entity_id: str,
        max_depth: int = 2,
    ) -> KnowledgeGraphSubgraph:
        """Extract a connected neighborhood subgraph around a root entity (M28)."""
        return self.epistemic_query_engine.query_subgraph(
            root_entity_id=root_entity_id,
            max_depth=max_depth,
        )

    # ---------------------------------------------------------
    # M29 Release Preflight & Validation
    # ---------------------------------------------------------
    def validate_release(self, config: Any | None = None) -> Any:
        """Execute automated preflight and release readiness validation (M29)."""
        return self.release_validator.run_preflight_checks(aura_instance=self, config=config)

    # ---------------------------------------------------------
    # M30 Durable Personal State
    # ---------------------------------------------------------
    def get_user_preferences(self) -> Any:
        """Retrieve current durable user preferences (M30)."""
        return self.durable_state_store.get_preferences()

    def update_user_preferences(self, preferences: Any) -> Any:
        """Update and persist durable user preferences (M30)."""
        return self.durable_state_store.update_preferences(preferences)

    def record_durable_memory(
        self,
        category: str,
        content: str,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> Any:
        """Record a unified durable memory entry (M30)."""
        return self.durable_state_store.record_memory(
            category=category,
            content=content,
            confidence=confidence,
            tags=tags,
        )

    def query_durable_memories(
        self,
        category: str | None = None,
        query: str = "",
        tag: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        """Query unified durable memory records (M30)."""
        return self.durable_state_store.query_memories(
            category=category,
            query=query,
            tag=tag,
            limit=limit,
        )

    # ---------------------------------------------------------
    # M31 Multi-Source Retrieval / Advanced RAG
    # ---------------------------------------------------------
    def retrieve_rag_context(self, query: str, max_chars: int = 4000) -> Any:
        """Execute multi-source RAG retrieval across memory, knowledge, experiences (M31)."""
        return self.retrieval_pipeline.execute_rag(query, max_context_chars=max_chars)

    def add_knowledge_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
    ) -> None:
        """Add a controlled knowledge document to retrieval pipeline (M31)."""
        self.retrieval_pipeline.add_knowledge_document(
            doc_id=doc_id,
            title=title,
            content=content,
            tags=tags,
        )

    # ---------------------------------------------------------
    # M32 Context & Personalization Engine
    # ---------------------------------------------------------
    def build_personalized_context(
        self,
        user_prompt: str,
        task_state: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> Any:
        """Build bounded, prioritized, and personalized context window (M32)."""
        prefs = self.durable_state_store.get_preferences()
        rag_bundle = self.retrieval_pipeline.execute_rag(user_prompt)
        return self.context_engine.build_context_bundle(
            user_prompt=user_prompt,
            user_preferences=prefs,
            retrieval_bundle=rag_bundle,
            conversation_history=history,
            task_state=task_state,
        )

    # ---------------------------------------------------------
    # M33 Structured Planning Engine
    # ---------------------------------------------------------
    def create_structured_plan(self, goal: str, steps: list[Any] | None = None) -> Any:
        """Create and validate a structured hierarchical plan (M33)."""
        return self.structured_planner.create_plan(goal=goal, steps=steps)

    def execute_structured_plan(self, plan: Any, timeout_seconds: float = 60.0) -> Any:
        """Execute a structured plan with dependency resolution and policy boundaries (M33)."""
        return self.structured_planner.execute_plan(
            plan=plan,
            tool_executor=self.tool_ecosystem,
            policy=self.policy,
            timeout_seconds=timeout_seconds,
        )

    def cancel_structured_plan(self, plan_id: str) -> bool:
        """Cancel a running or pending structured plan (M33)."""
        return self.structured_planner.cancel_plan(plan_id)

    # ---------------------------------------------------------
    # M34 Tool & Action Ecosystem
    # ---------------------------------------------------------
    def execute_ecosystem_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        caller: str = "agent",
    ) -> Any:
        """Execute an ecosystem tool within permission boundaries and audit log (M34)."""
        from core.tool_ecosystem_types import ToolExecutionRequest
        req = ToolExecutionRequest(
            tool_name=tool_name,
            parameters=parameters,
            caller_role=caller,
        )
        return self.tool_ecosystem.execute_tool(req, policy_engine=self.policy)

    def list_ecosystem_tools(self) -> list[Any]:
        """List all available tools in the action ecosystem (M34)."""
        return self.tool_ecosystem.list_tools()

    # ---------------------------------------------------------
    # M35 Proactive Assistance
    # ---------------------------------------------------------
    def evaluate_proactive_triggers(self, current_state: dict[str, Any] | None = None) -> list[Any]:
        """Evaluate system state against proactive triggers (M35)."""
        return self.proactive_engine.evaluate_triggers(current_state=current_state)

    def approve_proactive_proposal(self, proposal_id: str) -> Any:
        """Approve a pending proactive proposal (M35)."""
        return self.proactive_engine.approve_proposal(proposal_id)

    def reject_proactive_proposal(self, proposal_id: str, reason: str = "") -> Any:
        """Reject a pending proactive proposal (M35)."""
        return self.proactive_engine.reject_proposal(proposal_id, reason=reason)

    # ---------------------------------------------------------
    # M36 Experience & Learning Loop
    # ---------------------------------------------------------
    def record_interaction_outcome(self, outcome: Any) -> Any:
        """Record an execution outcome and distill learned heuristics (M36)."""
        return self.learning_engine.record_interaction(outcome)

    def query_learned_heuristics(self, task_pattern: str = "") -> list[Any]:
        """Query distilled behavioral heuristics (M36)."""
        return self.learning_engine.query_heuristics(task_pattern=task_pattern)

    def get_learning_report(self) -> Any:
        """Generate quantitative learning loop evaluation report (M36)."""
        return self.learning_engine.generate_report()

    # ---------------------------------------------------------
    # M37 Multimodal Foundation
    # ---------------------------------------------------------
    def process_multimodal_request(self, request: Any) -> Any:
        """Process multimodal content blocks across text, image, and audio (M37)."""
        return self.multimodal_processor.process_request(request)

    # ---------------------------------------------------------
    # M38 Device & Environment Integration
    # ---------------------------------------------------------
    def execute_device_action(
        self,
        device_id: str,
        capability: Any,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a capability on a target device environment (M38)."""
        from core.device_integration_types import DeviceActionRequest, DeviceCapability
        cap_enum = DeviceCapability(capability) if isinstance(capability, str) else capability
        req = DeviceActionRequest(
            action_id=f"act_{int(time.time())}",
            device_id=device_id,
            capability=cap_enum,
            parameters=parameters or {},
        )
        return self.device_engine.execute_action(req, policy_engine=self.policy)

    def list_devices(self) -> list[Any]:
        """List registered external devices and environments (M38)."""
        return self.device_engine.list_devices()

    # ---------------------------------------------------------
    # M39 Cross-Device State Sync
    # ---------------------------------------------------------
    def sync_cross_device_state(self, peer_engine: Any = None) -> int:
        """Synchronize state deltas with peer device node (M39)."""
        if peer_engine is not None:
            return self.cross_device_sync.sync_with_peer(peer_engine)
        return 0

    def get_cross_device_sync_status(self) -> Any:
        """Inspect vector clock and cross-device sync status (M39)."""
        return self.cross_device_sync.get_status()

    # ---------------------------------------------------------
    # M40 Integrated Personal Intelligence
    # ---------------------------------------------------------
    def execute_integrated_cycle(
        self,
        user_input: str | Any,
        task_id: str | None = None,
        auto_sync: bool = True,
    ) -> Any:
        """Execute complete end-to-end integrated personal intelligence cycle (M40)."""
        return self.integrated_intelligence_engine.execute_autonomous_cycle(
            input_request=user_input,
            task_id=task_id,
            auto_sync=auto_sync,
        )



