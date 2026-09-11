from core.trace_types import (
    SpanKind,
    SpanStatus,
    TraceContext,
    SpanEvent,
    SpanLink,
    SpanRecord,
)
from core.tracing import Tracer, Span
from core.trace_exporter import (
    TraceExporter,
    InMemoryTraceExporter,
    JsonlTraceExporter,
    OpenTelemetryDictExporter,
    CausalExecutionGraph,
)
from core.artifact_types import (
    Artifact,
    ArtifactType,
    compute_content_hash,
    infer_mime_type,
)
from core.artifact_store import (
    ArtifactStore,
    InMemoryArtifactStore,
    FileWorkspaceArtifactStore,
)
from core.artifact_manager import (
    ArtifactManager,
    ArtifactLineage,
)
from core.adaptive_optimizer import (
    TuningTarget,
    OptimizationEvent,
    OptimizationHistory,
    AdaptivePolicyOptimizer,
)
from core.feedback_bridge import FeedbackBridge

from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepDependency,
    StepStatus,
    deserialize_agent_plan,
    deserialize_execution_trace,
    deserialize_observation,
    serialize_agent_plan,
    serialize_execution_trace,
    serialize_observation,
)
from core.autonomous_agent import (
    AgentLoopConfig,
    AutonomousAgentExecutor,
    AutonomousAgentResult,
    is_recoverable_failure,
)
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
    GoalTrigger,
    TriggerType,
    deserialize_goal,
    deserialize_goal_observation,
    deserialize_goal_progress,
    deserialize_goal_trigger,
    serialize_goal,
    serialize_goal_observation,
    serialize_goal_progress,
    serialize_goal_trigger,
)
from core.goal_engine import GoalEngine, GoalEngineConfig
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_store import GoalStore, InMemoryGoalStore
from core.provenance import (
    TaintedValue,
    extract_provenance,
    is_tainted,
    render_for_prompt,
    unwrap_tainted,
    wrap_tainted,
)

__all__ = [

    # M24 Causal Tracing, Artifact Lifecycle & Adaptive Tuning
    "SpanKind",
    "SpanStatus",
    "TraceContext",
    "SpanEvent",
    "SpanLink",
    "SpanRecord",
    "Tracer",
    "Span",
    "TraceExporter",
    "InMemoryTraceExporter",
    "JsonlTraceExporter",
    "OpenTelemetryDictExporter",
    "CausalExecutionGraph",
    "Artifact",
    "ArtifactType",
    "compute_content_hash",
    "infer_mime_type",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "FileWorkspaceArtifactStore",
    "ArtifactManager",
    "ArtifactLineage",
    "TuningTarget",
    "OptimizationEvent",
    "OptimizationHistory",
    "AdaptivePolicyOptimizer",
    "FeedbackBridge",
    # M8.9 Provenance
    "TaintedValue",
    "wrap_tainted",
    "unwrap_tainted",
    "is_tainted",
    "extract_provenance",
    "render_for_prompt",
    # M10 Autonomous Agent Contracts & Loop
    "StepStatus",
    "StepDependency",
    "Observation",
    "AgentPlanStep",
    "AgentPlan",
    "ExecutionTrace",
    "serialize_observation",
    "deserialize_observation",
    "serialize_agent_plan",
    "deserialize_agent_plan",
    "serialize_execution_trace",
    "deserialize_execution_trace",
    "AgentLoopConfig",
    "AutonomousAgentResult",
    "AutonomousAgentExecutor",
    "is_recoverable_failure",
    # M11 Proactive / Goal-Oriented Intelligence
    "Goal",
    "GoalStatus",
    "GoalPriority",
    "TriggerType",
    "GoalTrigger",
    "GoalObservation",
    "GoalProgress",
    "serialize_goal",
    "deserialize_goal",
    "serialize_goal_progress",
    "deserialize_goal_progress",
    "serialize_goal_trigger",
    "deserialize_goal_trigger",
    "serialize_goal_observation",
    "deserialize_goal_observation",
    "GoalStore",
    "InMemoryGoalStore",
    "GoalEvaluationResult",
    "GoalReasoner",
    "GoalEngineConfig",
    "GoalEngine",
]
