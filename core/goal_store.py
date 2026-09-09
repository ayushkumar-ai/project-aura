import copy
import logging
from abc import ABC, abstractmethod
from typing import Any

from core.goal import Goal, GoalPriority, GoalStatus

logger = logging.getLogger("aura.goal_store")


class GoalStore(ABC):
    """Abstract contract for persisting and querying long-running Goals in AURA."""

    @abstractmethod
    def create(self, goal: Goal) -> Goal:
        """Persist a new Goal."""
        raise NotImplementedError

    @abstractmethod
    def get(self, goal_id: str) -> Goal:
        """Retrieve an existing Goal by ID."""
        raise NotImplementedError

    @abstractmethod
    def update(self, goal: Goal) -> None:
        """Update an existing Goal."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, goal_id: str) -> None:
        """Delete a Goal by ID."""
        raise NotImplementedError

    @abstractmethod
    def exists(self, goal_id: str) -> bool:
        """Check if a Goal exists by ID."""
        raise NotImplementedError

    @abstractmethod
    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        """List all goals matching optional status and priority filters."""
        raise NotImplementedError


class InMemoryGoalStore(GoalStore):
    """In-memory reference implementation of GoalStore with deepcopy isolation."""

    def __init__(self):
        self._goals: dict[str, Goal] = {}

    def create(self, goal: Goal) -> Goal:
        """Store a new Goal."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        if goal.goal_id in self._goals:
            raise ValueError(f"Goal with ID '{goal.goal_id}' already exists.")

        self._goals[goal.goal_id] = copy.deepcopy(goal)
        return copy.deepcopy(goal)

    def get(self, goal_id: str) -> Goal:
        """Retrieve a Goal by ID."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise KeyError(f"Invalid goal_id: '{goal_id}'")

        clean_id = goal_id.strip()
        if clean_id not in self._goals:
            raise KeyError(f"Goal '{clean_id}' not found.")

        return copy.deepcopy(self._goals[clean_id])

    def update(self, goal: Goal) -> None:
        """Update an existing Goal."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        if goal.goal_id not in self._goals:
            raise KeyError(f"Goal '{goal.goal_id}' does not exist.")

        self._goals[goal.goal_id] = copy.deepcopy(goal)

    def delete(self, goal_id: str) -> None:
        """Delete a Goal by ID."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise KeyError(f"Invalid goal_id: '{goal_id}'")

        clean_id = goal_id.strip()
        if clean_id not in self._goals:
            raise KeyError(f"Goal '{clean_id}' not found.")

        del self._goals[clean_id]

    def exists(self, goal_id: str) -> bool:
        """Check if a Goal exists."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            return False
        return goal_id.strip() in self._goals

    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        """List goals with optional filters."""
        results: list[Goal] = []
        for g in self._goals.values():
            if status is not None and g.status != status:
                continue
            if priority is not None and g.priority != priority:
                continue
            results.append(copy.deepcopy(g))
        return results
