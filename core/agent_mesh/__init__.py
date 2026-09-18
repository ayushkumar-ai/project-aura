"""M59 — Unified Autonomous Agent Runtime & Enterprise Intelligence Mesh Package."""

from core.agent_mesh.context import AgentContext, ContextFabric
from core.agent_mesh.dispatcher import ActionDispatcher
from core.agent_mesh.intent import ClassifiedIntent, IntentClassifier
from core.agent_mesh.learning import MemoryLearningBridge
from core.agent_mesh.mesh import IntelligenceMeshCoordinator
from core.agent_mesh.planner import StructuredPlanner
from core.agent_mesh.reflection import BoundedReflectionEngine, ReflectionDecision
from core.agent_mesh.runtime import UnifiedAgentRuntime
from core.agent_mesh.types import (
    ActionType,
    AgentDelegation,
    AgentMeshAudit,
    AgentMeshEvent,
    AgentPhase,
    AgentRole,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
    AgentRunStep,
    ExecutionPlan,
    FailureCategory,
    PlanStep,
    VerificationStatus,
)
from core.agent_mesh.validator import PlanValidator

__all__ = [
    "AgentContext",
    "ContextFabric",
    "ActionDispatcher",
    "ClassifiedIntent",
    "IntentClassifier",
    "MemoryLearningBridge",
    "IntelligenceMeshCoordinator",
    "StructuredPlanner",
    "BoundedReflectionEngine",
    "ReflectionDecision",
    "UnifiedAgentRuntime",
    "ActionType",
    "AgentDelegation",
    "AgentMeshAudit",
    "AgentMeshEvent",
    "AgentPhase",
    "AgentRole",
    "AgentRun",
    "AgentRunBudget",
    "AgentRunStatus",
    "AgentRunStep",
    "ExecutionPlan",
    "FailureCategory",
    "PlanStep",
    "VerificationStatus",
    "PlanValidator",
]
