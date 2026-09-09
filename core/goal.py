import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
    })

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives preserving TaintedValue."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif callable(val):
        raise ValueError("Cannot serialize callable value in goal or observation.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives restoring TaintedValue envelopes."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True and "raw_value" in val:
            return wrap_tainted(
                value=_restore_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=dict(val.get("metadata", {})),
            )
        return {k: _restore_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_restore_value(x) for x in val]
    return val


class GoalStatus(str, Enum):
    """Lifecycle status of a proactive, long-running Goal in AURA."""

    CREATED = "created"
    ACTIVE = "active"
    EVALUATING = "evaluating"
    ACTION_REQUIRED = "action_required"
    EXECUTING = "executing"
    PROGRESS_UPDATED = "progress_updated"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    BLOCKED = "blocked"

    def is_terminal(self) -> bool:
        """Check if goal has reached a final non-runnable state."""
        return self in (
            GoalStatus.COMPLETED,
            GoalStatus.CANCELLED,
            GoalStatus.EXPIRED,
            GoalStatus.FAILED,
        )

    def is_active(self) -> bool:
        """Check if goal is in an operational, non-terminal, non-paused state."""
        return self in (
            GoalStatus.ACTIVE,
            GoalStatus.EVALUATING,
            GoalStatus.ACTION_REQUIRED,
            GoalStatus.EXECUTING,
            GoalStatus.PROGRESS_UPDATED,
        )

    def is_paused(self) -> bool:
        """Check if goal is paused."""
        return self == GoalStatus.PAUSED


class GoalPriority(str, Enum):
    """Priority level of a Goal for evaluation and scheduling."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TriggerType(str, Enum):
    """Category of triggers that activate goal evaluation."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    STATE_CHANGE = "state_change"
    EVENT = "event"
    THRESHOLD = "threshold"


@dataclass(frozen=True)
class GoalTrigger:
    """Explicit, typed trigger condition that governs proactive goal evaluation."""

    trigger_id: str = field(default_factory=lambda: str(uuid4()))
    trigger_type: TriggerType = TriggerType.MANUAL
    expression: str = ""
    last_fired_at: float | None = None
    cooldown_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.trigger_id, str) or not self.trigger_id.strip():
            raise ValueError("trigger_id must be a non-empty string.")
        object.__setattr__(self, "trigger_id", self.trigger_id.strip())

        if isinstance(self.trigger_type, str):
            object.__setattr__(self, "trigger_type", TriggerType(self.trigger_type))
        elif not isinstance(self.trigger_type, TriggerType):
            raise TypeError("trigger_type must be an instance of TriggerType.")

        if not isinstance(self.expression, str):
            raise TypeError("expression must be a string.")
        object.__setattr__(self, "expression", self.expression.strip())

        if self.last_fired_at is not None and not isinstance(self.last_fired_at, (int, float)):
            raise TypeError("last_fired_at must be numeric or None.")
        if self.last_fired_at is not None:
            object.__setattr__(self, "last_fired_at", float(self.last_fired_at))

        if not isinstance(self.cooldown_seconds, (int, float)) or self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative number.")
        object.__setattr__(self, "cooldown_seconds", float(self.cooldown_seconds))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def is_ready(self, current_time: float | None = None, context: dict[str, Any] | None = None) -> bool:
        """Check whether the trigger is ready to fire given current time and cooldown."""
        now = current_time if current_time is not None else time.time()
        if self.last_fired_at is not None:
            if (now - self.last_fired_at) < self.cooldown_seconds:
                return False

        if self.trigger_type == TriggerType.SCHEDULE and self.expression:
            try:
                interval = float(self.expression)
                if self.last_fired_at is not None:
                    return (now - self.last_fired_at) >= interval
                return True
            except ValueError:
                pass

        if self.trigger_type == TriggerType.STATE_CHANGE:
            if context is None:
                return False
            key = self.expression
            return bool(key and key in context)

        if self.trigger_type == TriggerType.EVENT:
            if context is None:
                return False
            event_name = context.get("event")
            return bool(event_name and event_name == self.expression)

        return True

    def fire(self, current_time: float | None = None) -> "GoalTrigger":
        """Return a new GoalTrigger with updated last_fired_at timestamp."""
        now = current_time if current_time is not None else time.time()
        return GoalTrigger(
            trigger_id=self.trigger_id,
            trigger_type=self.trigger_type,
            expression=self.expression,
            last_fired_at=now,
            cooldown_seconds=self.cooldown_seconds,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class GoalObservation:
    """Observation or contextual signal ingested during goal evaluation."""

    observation_id: str = field(default_factory=lambda: str(uuid4()))
    goal_id: str = ""
    source: str = ""
    data: Any = None
    is_untrusted: bool = False
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.observation_id, str) or not self.observation_id.strip():
            raise ValueError("observation_id must be a non-empty string.")
        object.__setattr__(self, "observation_id", self.observation_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if not isinstance(self.source, str):
            raise TypeError("source must be a string.")
        object.__setattr__(self, "source", self.source.strip())

        if callable(self.data):
            raise ValueError("GoalObservation data cannot be a callable.")

        untrusted = bool(self.is_untrusted)
        if isinstance(self.data, TaintedValue):
            untrusted = untrusted or self.data.is_untrusted
        object.__setattr__(self, "is_untrusted", untrusted)

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be numeric.")
        object.__setattr__(self, "timestamp", float(self.timestamp))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class GoalProgress:
    """Quantitative and qualitative progress tracking for an active Goal."""

    percentage: float = 0.0
    current_stage: str = "initial"
    satisfied_criteria: tuple[str, ...] = field(default_factory=tuple)
    remaining_criteria: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    summary: str = ""
    last_evaluated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.percentage, (int, float)):
            raise TypeError("percentage must be a number between 0.0 and 1.0.")
        pct = max(0.0, min(1.0, float(self.percentage)))
        object.__setattr__(self, "percentage", pct)

        if not isinstance(self.current_stage, str):
            raise TypeError("current_stage must be a string.")
        object.__setattr__(self, "current_stage", self.current_stage.strip())

        if isinstance(self.satisfied_criteria, (list, tuple, set)):
            object.__setattr__(self, "satisfied_criteria", tuple(str(c).strip() for c in self.satisfied_criteria if str(c).strip()))
        else:
            raise TypeError("satisfied_criteria must be a sequence of strings.")

        if isinstance(self.remaining_criteria, (list, tuple, set)):
            object.__setattr__(self, "remaining_criteria", tuple(str(c).strip() for c in self.remaining_criteria if str(c).strip()))
        else:
            raise TypeError("remaining_criteria must be a sequence of strings.")

        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a number between 0.0 and 1.0.")
        conf = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", conf)

        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string.")
        object.__setattr__(self, "summary", self.summary.strip())

        if not isinstance(self.last_evaluated_at, (int, float)):
            raise TypeError("last_evaluated_at must be numeric.")
        object.__setattr__(self, "last_evaluated_at", float(self.last_evaluated_at))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def is_complete(self) -> bool:
        """Check if progress indicates 100% completion and all criteria satisfied."""
        return self.percentage >= 1.0 and len(self.remaining_criteria) == 0


@dataclass(frozen=True)
class Goal:
    """Explicit, immutable long-running Goal entity in Project AURA."""

    goal_id: str = field(default_factory=lambda: str(uuid4()))
    title: str = ""
    description: str = ""
    success_criteria: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    priority: GoalPriority = GoalPriority.MEDIUM
    status: GoalStatus = GoalStatus.CREATED
    triggers: tuple[GoalTrigger, ...] = field(default_factory=tuple)
    progress: GoalProgress = field(default_factory=GoalProgress)
    evaluation_count: int = 0
    action_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_goal_id: str | None = None
    subgoal_ids: tuple[str, ...] = field(default_factory=tuple)
    depends_on_goal_ids: tuple[str, ...] = field(default_factory=tuple)
    depth: int = 0
    executed_task_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string.")
        object.__setattr__(self, "title", self.title.strip())

        if not isinstance(self.description, str):
            raise TypeError("description must be a string.")
        object.__setattr__(self, "description", self.description.strip())

        if isinstance(self.success_criteria, (list, tuple, set)):
            object.__setattr__(self, "success_criteria", tuple(str(c).strip() for c in self.success_criteria if str(c).strip()))
        else:
            raise TypeError("success_criteria must be a sequence of strings.")

        if isinstance(self.constraints, (list, tuple, set)):
            object.__setattr__(self, "constraints", tuple(str(c).strip() for c in self.constraints if str(c).strip()))
        else:
            raise TypeError("constraints must be a sequence of strings.")

        if isinstance(self.priority, str):
            object.__setattr__(self, "priority", GoalPriority(self.priority))
        elif not isinstance(self.priority, GoalPriority):
            raise TypeError("priority must be an instance of GoalPriority.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", GoalStatus(self.status))
        elif not isinstance(self.status, GoalStatus):
            raise TypeError("status must be an instance of GoalStatus.")

        if isinstance(self.triggers, (list, tuple)):
            for t in self.triggers:
                if not isinstance(t, GoalTrigger):
                    raise TypeError("All triggers must be GoalTrigger instances.")
            object.__setattr__(self, "triggers", tuple(self.triggers))
        else:
            raise TypeError("triggers must be a sequence of GoalTrigger instances.")

        if not isinstance(self.progress, GoalProgress):
            raise TypeError("progress must be an instance of GoalProgress.")

        if not isinstance(self.evaluation_count, int) or self.evaluation_count < 0:
            raise ValueError("evaluation_count must be a non-negative integer.")

        if not isinstance(self.action_count, int) or self.action_count < 0:
            raise ValueError("action_count must be a non-negative integer.")

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")
        if not isinstance(self.updated_at, (int, float)):
            raise TypeError("updated_at must be numeric.")

        if self.expires_at is not None:
            if not isinstance(self.expires_at, (int, float)):
                raise TypeError("expires_at must be numeric or None.")
            object.__setattr__(self, "expires_at", float(self.expires_at))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

        # Hierarchical validation (M12)
        if self.parent_goal_id is not None:
            if not isinstance(self.parent_goal_id, str) or not self.parent_goal_id.strip():
                raise ValueError("parent_goal_id must be a non-empty string or None.")
            clean_parent = self.parent_goal_id.strip()
            if clean_parent == self.goal_id:
                raise ValueError(f"Goal '{self.goal_id}' cannot be its own parent.")
            object.__setattr__(self, "parent_goal_id", clean_parent)

        if isinstance(self.subgoal_ids, (list, tuple, set)):
            sub_list = [str(s).strip() for s in self.subgoal_ids if str(s).strip()]
            if self.goal_id in sub_list:
                raise ValueError(f"Goal '{self.goal_id}' cannot include itself as a subgoal.")
            if self.parent_goal_id and self.parent_goal_id in sub_list:
                raise ValueError(f"Goal '{self.goal_id}' cannot include parent '{self.parent_goal_id}' as a subgoal.")
            if len(sub_list) != len(set(sub_list)):
                raise ValueError("subgoal_ids contains duplicate entries.")
            if len(sub_list) > 5:
                raise ValueError(f"Sub-goal count ({len(sub_list)}) exceeds maximum allowed (5).")
            object.__setattr__(self, "subgoal_ids", tuple(sub_list))
        else:
            raise TypeError("subgoal_ids must be a sequence of strings.")

        if isinstance(self.depends_on_goal_ids, (list, tuple, set)):
            dep_list = [str(d).strip() for d in self.depends_on_goal_ids if str(d).strip()]
            if self.goal_id in dep_list:
                raise ValueError(f"Goal '{self.goal_id}' cannot depend on itself.")
            if len(dep_list) != len(set(dep_list)):
                raise ValueError("depends_on_goal_ids contains duplicate entries.")
            if len(dep_list) > 10:
                raise ValueError(f"Dependency count ({len(dep_list)}) exceeds maximum allowed (10).")
            object.__setattr__(self, "depends_on_goal_ids", tuple(dep_list))
        else:
            raise TypeError("depends_on_goal_ids must be a sequence of strings.")

        if not isinstance(self.depth, int) or self.depth < 0:
            raise ValueError("depth must be a non-negative integer.")
        if self.depth > 3:
            raise ValueError(f"Goal hierarchy depth ({self.depth}) exceeds maximum allowed (3).")

        if isinstance(self.executed_task_ids, (list, tuple, set)):
            tasks = [str(t).strip() for t in self.executed_task_ids if str(t).strip()]
            object.__setattr__(self, "executed_task_ids", tuple(tasks))
        else:
            raise TypeError("executed_task_ids must be a sequence of strings.")

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if the goal has passed its expiration time."""
        if self.expires_at is None:
            return False
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def with_status(self, status: GoalStatus, updated_at: float | None = None) -> "Goal":
        """Return a new immutable Goal with updated status."""
        now = updated_at if updated_at is not None else time.time()
        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=status,
            triggers=self.triggers,
            progress=self.progress,
            evaluation_count=self.evaluation_count,
            action_count=self.action_count,
            created_at=self.created_at,
            updated_at=now,
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=self.subgoal_ids,
            depends_on_goal_ids=self.depends_on_goal_ids,
            depth=self.depth,
            executed_task_ids=self.executed_task_ids,
        )

    def with_progress(
        self,
        progress: GoalProgress,
        status: GoalStatus | None = None,
        evaluation_count: int | None = None,
        action_count: int | None = None,
        updated_at: float | None = None,
    ) -> "Goal":
        """Return a new immutable Goal with updated progress and metrics."""
        now = updated_at if updated_at is not None else time.time()
        if status is not None:
            new_status = status
        else:
            new_status = GoalStatus.COMPLETED if progress.is_complete() and not self.status.is_terminal() else self.status

        new_eval = evaluation_count if evaluation_count is not None else self.evaluation_count
        new_act = action_count if action_count is not None else self.action_count

        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=new_status,
            triggers=self.triggers,
            progress=progress,
            evaluation_count=new_eval,
            action_count=new_act,
            created_at=self.created_at,
            updated_at=now,
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=self.subgoal_ids,
            depends_on_goal_ids=self.depends_on_goal_ids,
            depth=self.depth,
            executed_task_ids=self.executed_task_ids,
        )

    def with_updated_trigger(self, trigger_id: str, fired_at: float | None = None) -> "Goal":
        """Return a new Goal with the specified trigger updated to fired state."""
        now = fired_at if fired_at is not None else time.time()
        new_triggers = [
            t.fire(now) if t.trigger_id == trigger_id else t
            for t in self.triggers
        ]
        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=self.status,
            triggers=tuple(new_triggers),
            progress=self.progress,
            evaluation_count=self.evaluation_count,
            action_count=self.action_count,
            created_at=self.created_at,
            updated_at=now,
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=self.subgoal_ids,
            depends_on_goal_ids=self.depends_on_goal_ids,
            depth=self.depth,
            executed_task_ids=self.executed_task_ids,
        )

    def with_subgoal(self, subgoal_id: str) -> "Goal":
        """Return a new Goal with the specified subgoal ID appended."""
        clean_id = str(subgoal_id).strip()
        if not clean_id:
            raise ValueError("subgoal_id must be a non-empty string.")
        if clean_id in self.subgoal_ids:
            return self
        new_subgoals = self.subgoal_ids + (clean_id,)
        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=self.status,
            triggers=self.triggers,
            progress=self.progress,
            evaluation_count=self.evaluation_count,
            action_count=self.action_count,
            created_at=self.created_at,
            updated_at=time.time(),
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=new_subgoals,
            depends_on_goal_ids=self.depends_on_goal_ids,
            depth=self.depth,
            executed_task_ids=self.executed_task_ids,
        )

    def with_dependency(self, dependency_goal_id: str) -> "Goal":
        """Return a new Goal with the specified dependency goal ID appended."""
        clean_id = str(dependency_goal_id).strip()
        if not clean_id:
            raise ValueError("dependency_goal_id must be a non-empty string.")
        if clean_id in self.depends_on_goal_ids:
            return self
        new_deps = self.depends_on_goal_ids + (clean_id,)
        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=self.status,
            triggers=self.triggers,
            progress=self.progress,
            evaluation_count=self.evaluation_count,
            action_count=self.action_count,
            created_at=self.created_at,
            updated_at=time.time(),
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=self.subgoal_ids,
            depends_on_goal_ids=new_deps,
            depth=self.depth,
            executed_task_ids=self.executed_task_ids,
        )

    def with_executed_task(self, task_id: str) -> "Goal":
        """Return a new Goal with the specified task ID recorded in lineage."""
        clean_id = str(task_id).strip()
        if not clean_id:
            raise ValueError("task_id must be a non-empty string.")
        if clean_id in self.executed_task_ids:
            return self
        new_tasks = self.executed_task_ids + (clean_id,)
        return Goal(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            success_criteria=self.success_criteria,
            constraints=self.constraints,
            priority=self.priority,
            status=self.status,
            triggers=self.triggers,
            progress=self.progress,
            evaluation_count=self.evaluation_count,
            action_count=self.action_count,
            created_at=self.created_at,
            updated_at=time.time(),
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
            parent_goal_id=self.parent_goal_id,
            subgoal_ids=self.subgoal_ids,
            depends_on_goal_ids=self.depends_on_goal_ids,
            depth=self.depth,
            executed_task_ids=new_tasks,
        )


def detect_dependency_cycles(goals: dict[str, Goal] | list[Goal] | tuple[Goal, ...]) -> list[list[str]]:
    """Detect cycles in goal dependencies using depth-first search graph coloring."""
    goal_map: dict[str, Goal] = (
        goals if isinstance(goals, dict) else {g.goal_id: g for g in goals}
    )

    # 0 = unvisited, 1 = visiting (in stack), 2 = visited
    state: dict[str, int] = {gid: 0 for gid in goal_map}
    cycles: list[list[str]] = []

    def dfs(node_id: str, path: list[str]) -> None:
        state[node_id] = 1
        path.append(node_id)

        node = goal_map.get(node_id)
        if node is not None:
            for dep_id in node.depends_on_goal_ids:
                if dep_id not in goal_map:
                    continue
                dep_state = state.get(dep_id, 0)
                if dep_state == 1:
                    # Cycle detected! Extract cycle path
                    cycle_start = path.index(dep_id)
                    cycle_subpath = path[cycle_start:] + [dep_id]
                    cycles.append(cycle_subpath)
                elif dep_state == 0:
                    dfs(dep_id, path)

        path.pop()
        state[node_id] = 2

    for gid in list(goal_map.keys()):
        if state[gid] == 0:
            dfs(gid, [])

    return cycles


def validate_goal_hierarchy(
    goals: dict[str, Goal] | list[Goal] | tuple[Goal, ...],
    max_depth: int = 3,
    max_subgoals_per_parent: int = 5,
) -> None:
    """Validate hierarchy consistency, depth constraints, and cycle absence across goals."""
    goal_map: dict[str, Goal] = (
        goals if isinstance(goals, dict) else {g.goal_id: g for g in goals}
    )

    # 1. Check dependency cycles
    cycles = detect_dependency_cycles(goal_map)
    if cycles:
        cycle_strs = [" -> ".join(c) for c in cycles]
        raise ValueError(f"Goal dependency cycles detected: {', '.join(cycle_strs)}")

    # 2. Check parent-child hierarchy consistency and depth
    for g in goal_map.values():
        if g.depth > max_depth:
            raise ValueError(f"Goal '{g.goal_id}' depth ({g.depth}) exceeds maximum ({max_depth}).")

        if len(g.subgoal_ids) > max_subgoals_per_parent:
            raise ValueError(
                f"Goal '{g.goal_id}' subgoal count ({len(g.subgoal_ids)}) exceeds maximum ({max_subgoals_per_parent})."
            )

        if g.parent_goal_id:
            parent = goal_map.get(g.parent_goal_id)
            if parent is not None:
                if g.depth != parent.depth + 1:
                    raise ValueError(
                        f"Child goal '{g.goal_id}' depth ({g.depth}) must be parent depth ({parent.depth}) + 1."
                    )
                # Check for parent loop: trace ancestor chain
                visited_ancestors = {g.goal_id}
                curr = parent
                while curr is not None:
                    if curr.goal_id in visited_ancestors:
                        raise ValueError(f"Parent-child cycle detected involving goal '{g.goal_id}'.")
                    visited_ancestors.add(curr.goal_id)
                    curr = goal_map.get(curr.parent_goal_id) if curr.parent_goal_id else None


def get_topological_evaluation_order(
    goals: list[Goal] | tuple[Goal, ...] | dict[str, Goal]
) -> list[Goal]:
    """Compute a deterministic topological sort order where dependencies and subgoals precede dependent goals."""
    goal_map: dict[str, Goal] = (
        goals if isinstance(goals, dict) else {g.goal_id: g for g in goals}
    )

    cycles = detect_dependency_cycles(goal_map)
    if cycles:
        cycle_strs = [" -> ".join(c) for c in cycles]
        raise ValueError(f"Cannot topologically sort goals with dependency cycles: {', '.join(cycle_strs)}")

    visited: set[str] = set()
    result: list[Goal] = []

    def visit(gid: str) -> None:
        if gid in visited:
            return
        visited.add(gid)

        g = goal_map.get(gid)
        if g is not None:
            # First visit subgoals so subgoals evaluate before parents
            for sub_id in g.subgoal_ids:
                if sub_id in goal_map:
                    visit(sub_id)
            # Next visit prerequisites so dependencies evaluate before dependents
            for dep_id in g.depends_on_goal_ids:
                if dep_id in goal_map:
                    visit(dep_id)
            result.append(g)

    # Sort keys for deterministic traversal
    for gid in sorted(goal_map.keys()):
        visit(gid)

    return result


def serialize_goal_trigger(trigger: GoalTrigger) -> dict[str, Any]:
    """Serialize a GoalTrigger into a deterministic dictionary."""
    if not isinstance(trigger, GoalTrigger):
        raise TypeError("trigger must be a GoalTrigger instance.")
    return {
        "trigger_id": trigger.trigger_id,
        "trigger_type": trigger.trigger_type.value,
        "expression": trigger.expression,
        "last_fired_at": trigger.last_fired_at,
        "cooldown_seconds": trigger.cooldown_seconds,
        "metadata": _canonical_value(trigger.metadata),
    }


def deserialize_goal_trigger(data: dict[str, Any]) -> GoalTrigger:
    """Deserialize a GoalTrigger from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")
    return GoalTrigger(
        trigger_id=str(data.get("trigger_id", uuid4())),
        trigger_type=TriggerType(data.get("trigger_type", TriggerType.MANUAL.value)),
        expression=str(data.get("expression", "")),
        last_fired_at=data.get("last_fired_at"),
        cooldown_seconds=float(data.get("cooldown_seconds", 0.0)),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_goal_observation(obs: GoalObservation) -> dict[str, Any]:
    """Serialize a GoalObservation preserving TaintedValue envelopes."""
    if not isinstance(obs, GoalObservation):
        raise TypeError("obs must be a GoalObservation instance.")
    return {
        "observation_id": obs.observation_id,
        "goal_id": obs.goal_id,
        "source": obs.source,
        "data": _canonical_value(obs.data),
        "is_untrusted": obs.is_untrusted,
        "timestamp": obs.timestamp,
        "metadata": _canonical_value(obs.metadata),
    }


def deserialize_goal_observation(data: dict[str, Any]) -> GoalObservation:
    """Deserialize a GoalObservation restoring TaintedValue envelopes."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")
    return GoalObservation(
        observation_id=str(data.get("observation_id", uuid4())),
        goal_id=str(data.get("goal_id", "")),
        source=str(data.get("source", "")),
        data=_restore_value(data.get("data")),
        is_untrusted=bool(data.get("is_untrusted", False)),
        timestamp=float(data.get("timestamp", time.time())),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_goal_progress(progress: GoalProgress) -> dict[str, Any]:
    """Serialize GoalProgress into a dictionary."""
    if not isinstance(progress, GoalProgress):
        raise TypeError("progress must be a GoalProgress instance.")
    return {
        "percentage": progress.percentage,
        "current_stage": progress.current_stage,
        "satisfied_criteria": list(progress.satisfied_criteria),
        "remaining_criteria": list(progress.remaining_criteria),
        "confidence": progress.confidence,
        "summary": progress.summary,
        "last_evaluated_at": progress.last_evaluated_at,
        "metadata": _canonical_value(progress.metadata),
    }


def deserialize_goal_progress(data: dict[str, Any]) -> GoalProgress:
    """Deserialize GoalProgress from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")
    return GoalProgress(
        percentage=float(data.get("percentage", 0.0)),
        current_stage=str(data.get("current_stage", "initial")),
        satisfied_criteria=tuple(data.get("satisfied_criteria", ())),
        remaining_criteria=tuple(data.get("remaining_criteria", ())),
        confidence=float(data.get("confidence", 1.0)),
        summary=str(data.get("summary", "")),
        last_evaluated_at=float(data.get("last_evaluated_at", time.time())),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_goal(goal: Goal) -> dict[str, Any]:
    """Serialize a Goal into a deterministic dictionary."""
    if not isinstance(goal, Goal):
        raise TypeError("goal must be a Goal instance.")
    return {
        "goal_id": goal.goal_id,
        "title": goal.title,
        "description": goal.description,
        "success_criteria": list(goal.success_criteria),
        "constraints": list(goal.constraints),
        "priority": goal.priority.value,
        "status": goal.status.value,
        "triggers": [serialize_goal_trigger(t) for t in goal.triggers],
        "progress": serialize_goal_progress(goal.progress),
        "evaluation_count": goal.evaluation_count,
        "action_count": goal.action_count,
        "created_at": goal.created_at,
        "updated_at": goal.updated_at,
        "expires_at": goal.expires_at,
        "metadata": _canonical_value(goal.metadata),
        "parent_goal_id": goal.parent_goal_id,
        "subgoal_ids": list(goal.subgoal_ids),
        "depends_on_goal_ids": list(goal.depends_on_goal_ids),
        "depth": goal.depth,
        "executed_task_ids": list(goal.executed_task_ids),
    }


def deserialize_goal(data: dict[str, Any]) -> Goal:
    """Deserialize a Goal from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    triggers = [
        deserialize_goal_trigger(td)
        for td in data.get("triggers", [])
        if isinstance(td, dict)
    ]

    prog_d = data.get("progress")
    progress = (
        deserialize_goal_progress(prog_d)
        if isinstance(prog_d, dict)
        else GoalProgress()
    )

    return Goal(
        goal_id=str(data.get("goal_id", uuid4())),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        success_criteria=tuple(data.get("success_criteria", ())),
        constraints=tuple(data.get("constraints", ())),
        priority=GoalPriority(data.get("priority", GoalPriority.MEDIUM.value)),
        status=GoalStatus(data.get("status", GoalStatus.CREATED.value)),
        triggers=tuple(triggers),
        progress=progress,
        evaluation_count=int(data.get("evaluation_count", 0)),
        action_count=int(data.get("action_count", 0)),
        created_at=float(data.get("created_at", time.time())),
        updated_at=float(data.get("updated_at", time.time())),
        expires_at=data.get("expires_at"),
        metadata=_restore_value(dict(data.get("metadata", {}))),
        parent_goal_id=data.get("parent_goal_id"),
        subgoal_ids=tuple(data.get("subgoal_ids", ())),
        depends_on_goal_ids=tuple(data.get("depends_on_goal_ids", ())),
        depth=int(data.get("depth", 0)),
        executed_task_ids=tuple(data.get("executed_task_ids", ())),
    )
