"""M53 — Proactive Automations, Scheduled Triggers & Autonomous Supervisor."""

from core.automations.types import (
    ActionTemplate,
    Automation,
    AutomationCycleDetectedError,
    AutomationError,
    AutomationNotFoundError,
    AutomationQuotaExceededError,
    AutomationRecursionLimitExceededError,
    AutomationRun,
    AutomationStatus,
    AutomationValidationError,
    CatchUpPolicy,
    ConditionConfig,
    ConditionEvaluationError,
    LeaseFencingError,
    LockTimeoutError,
    RunStatus,
    StatementTimeoutError,
    TransactionDeadlineExceededError,
    TriggerConfig,
    TriggerType,
)
from core.automations.cron_parser import (
    CronExpression,
    calculate_next_fire,
    validate_cron,
)
from core.automations.context_provider import ReadOnlyContextProvider
from core.automations.condition_engine import ConditionEngine, validate_predicate_ast
from core.automations.scheduler import AutomationScheduler
from core.automations.reconciler import AutomationReconciler
from core.automations.supervisor import AutonomousSupervisor

__all__ = [
    "ActionTemplate",
    "Automation",
    "AutomationCycleDetectedError",
    "AutomationError",
    "AutomationNotFoundError",
    "AutomationQuotaExceededError",
    "AutomationRecursionLimitExceededError",
    "AutomationReconciler",
    "AutomationRun",
    "AutomationScheduler",
    "AutomationStatus",
    "AutomationValidationError",
    "AutonomousSupervisor",
    "CatchUpPolicy",
    "ConditionConfig",
    "ConditionEngine",
    "ConditionEvaluationError",
    "CronExpression",
    "LeaseFencingError",
    "LockTimeoutError",
    "ReadOnlyContextProvider",
    "RunStatus",
    "StatementTimeoutError",
    "TransactionDeadlineExceededError",
    "TriggerConfig",
    "TriggerType",
    "calculate_next_fire",
    "validate_cron",
    "validate_predicate_ast",
]
