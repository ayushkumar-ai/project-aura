import copy
import json
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalStatus,
    deserialize_goal,
    deserialize_goal_observation,
    serialize_goal,
    serialize_goal_observation,
)

logger = logging.getLogger("aura.goal_store")


class GoalStore(ABC):
    """Abstract contract for persisting and querying long-running Goals and observations in AURA."""

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
        parent_goal_id: str | None = None,
    ) -> list[Goal]:
        """List all goals matching optional status, priority, and parent filters."""
        raise NotImplementedError

    @abstractmethod
    def add_observation(self, goal_id: str, observation: GoalObservation) -> GoalObservation:
        """Persist a contextual observation for a goal."""
        raise NotImplementedError

    @abstractmethod
    def get_observations(self, goal_id: str) -> list[GoalObservation]:
        """Retrieve all observations persisted for a goal."""
        raise NotImplementedError

    @abstractmethod
    def clear_observations(self, goal_id: str) -> None:
        """Clear all observations persisted for a goal."""
        raise NotImplementedError

    @abstractmethod
    def get_subgoals(self, parent_goal_id: str) -> list[Goal]:
        """Retrieve all subgoals of a specific parent goal."""
        raise NotImplementedError

    @abstractmethod
    def get_root_goals(self) -> list[Goal]:
        """Retrieve all top-level root goals (goals with no parent)."""
        raise NotImplementedError


class InMemoryGoalStore(GoalStore):
    """In-memory reference implementation of GoalStore with deepcopy isolation and bounded observations."""

    def __init__(self, max_observations_per_goal: int = 50):
        if not isinstance(max_observations_per_goal, int) or max_observations_per_goal <= 0:
            raise ValueError("max_observations_per_goal must be a positive integer.")
        self.max_observations_per_goal = max_observations_per_goal
        self._goals: dict[str, Goal] = {}
        self._observations: dict[str, list[GoalObservation]] = {}

    def create(self, goal: Goal) -> Goal:
        """Store a new Goal."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        if goal.goal_id in self._goals:
            raise ValueError(f"Goal with ID '{goal.goal_id}' already exists.")

        self._goals[goal.goal_id] = copy.deepcopy(goal)
        if goal.goal_id not in self._observations:
            self._observations[goal.goal_id] = []
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
        """Delete a Goal by ID and clean up its observations."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise KeyError(f"Invalid goal_id: '{goal_id}'")

        clean_id = goal_id.strip()
        if clean_id not in self._goals:
            raise KeyError(f"Goal '{clean_id}' not found.")

        del self._goals[clean_id]
        self._observations.pop(clean_id, None)

    def exists(self, goal_id: str) -> bool:
        """Check if a Goal exists."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            return False
        return goal_id.strip() in self._goals

    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
        parent_goal_id: str | None = None,
    ) -> list[Goal]:
        """List goals with optional filters."""
        results: list[Goal] = []
        for g in self._goals.values():
            if status is not None and g.status != status:
                continue
            if priority is not None and g.priority != priority:
                continue
            if parent_goal_id is not None and g.parent_goal_id != parent_goal_id:
                continue
            results.append(copy.deepcopy(g))
        return results

    def add_observation(self, goal_id: str, observation: GoalObservation) -> GoalObservation:
        """Persist a contextual observation for a goal with bounded retention."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")
        if not isinstance(observation, GoalObservation):
            raise TypeError("observation must be a GoalObservation instance.")

        clean_id = goal_id.strip()
        if clean_id not in self._observations:
            self._observations[clean_id] = []

        obs_copy = copy.deepcopy(observation)
        self._observations[clean_id].append(obs_copy)

        # Enforce maximum observation ring buffer
        if len(self._observations[clean_id]) > self.max_observations_per_goal:
            self._observations[clean_id] = self._observations[clean_id][-self.max_observations_per_goal:]

        return copy.deepcopy(obs_copy)

    def get_observations(self, goal_id: str) -> list[GoalObservation]:
        """Retrieve all observations for a goal."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")

        clean_id = goal_id.strip()
        return [copy.deepcopy(o) for o in self._observations.get(clean_id, [])]

    def clear_observations(self, goal_id: str) -> None:
        """Clear all observations for a goal."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")

        clean_id = goal_id.strip()
        self._observations[clean_id] = []

    def get_subgoals(self, parent_goal_id: str) -> list[Goal]:
        """Retrieve all subgoals of a specific parent goal."""
        clean_parent = parent_goal_id.strip() if parent_goal_id else ""
        return [
            copy.deepcopy(g)
            for g in self._goals.values()
            if g.parent_goal_id == clean_parent
        ]

    def get_root_goals(self) -> list[Goal]:
        """Retrieve all top-level root goals."""
        return [
            copy.deepcopy(g)
            for g in self._goals.values()
            if g.parent_goal_id is None
        ]


class FileGoalStore(GoalStore):
    """File-backed persistent GoalStore implementation preserving TaintedValue provenance."""

    def __init__(self, storage_dir: str | Path, max_observations_per_goal: int = 50):
        if not isinstance(max_observations_per_goal, int) or max_observations_per_goal <= 0:
            raise ValueError("max_observations_per_goal must be a positive integer.")

        self.storage_dir = Path(storage_dir)
        self.max_observations_per_goal = max_observations_per_goal
        self.goals_dir = self.storage_dir / "goals"
        self.observations_dir = self.storage_dir / "observations"

        self.goals_dir.mkdir(parents=True, exist_ok=True)
        self.observations_dir.mkdir(parents=True, exist_ok=True)

    def _goal_path(self, goal_id: str) -> Path:
        return self.goals_dir / f"{goal_id}.json"

    def _obs_path(self, goal_id: str) -> Path:
        return self.observations_dir / f"{goal_id}.json"

    def _write_json_atomic(self, path: Path, data: Any) -> None:
        temp_file = tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(data, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            shutil.move(temp_file.name, str(path))
        except Exception:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)
            raise

    def create(self, goal: Goal) -> Goal:
        """Persist a new Goal to disk."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        path = self._goal_path(goal.goal_id)
        if path.exists():
            raise ValueError(f"Goal with ID '{goal.goal_id}' already exists.")

        payload = serialize_goal(goal)
        self._write_json_atomic(path, payload)
        return copy.deepcopy(goal)

    def get(self, goal_id: str) -> Goal:
        """Retrieve a Goal by ID from disk."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise KeyError(f"Invalid goal_id: '{goal_id}'")

        clean_id = goal_id.strip()
        path = self._goal_path(clean_id)
        if not path.exists():
            raise KeyError(f"Goal '{clean_id}' not found.")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return deserialize_goal(data)

    def update(self, goal: Goal) -> None:
        """Update an existing Goal on disk."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        path = self._goal_path(goal.goal_id)
        if not path.exists():
            raise KeyError(f"Goal '{goal.goal_id}' does not exist.")

        payload = serialize_goal(goal)
        self._write_json_atomic(path, payload)

    def delete(self, goal_id: str) -> None:
        """Delete a Goal and its observations from disk."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise KeyError(f"Invalid goal_id: '{goal_id}'")

        clean_id = goal_id.strip()
        path = self._goal_path(clean_id)
        if not path.exists():
            raise KeyError(f"Goal '{clean_id}' not found.")

        path.unlink()
        obs_path = self._obs_path(clean_id)
        if obs_path.exists():
            obs_path.unlink()

    def exists(self, goal_id: str) -> bool:
        """Check if a Goal file exists on disk."""
        if not isinstance(goal_id, str) or not goal_id.strip():
            return False
        return self._goal_path(goal_id.strip()).exists()

    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
        parent_goal_id: str | None = None,
    ) -> list[Goal]:
        """List all goals stored on disk matching filters."""
        results: list[Goal] = []
        for file in sorted(self.goals_dir.glob("*.json")):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                goal = deserialize_goal(data)
                if status is not None and goal.status != status:
                    continue
                if priority is not None and goal.priority != priority:
                    continue
                if parent_goal_id is not None and goal.parent_goal_id != parent_goal_id:
                    continue
                results.append(goal)
            except Exception as e:
                logger.warning("Error reading goal file '%s': %s", file, e)
        return results

    def add_observation(self, goal_id: str, observation: GoalObservation) -> GoalObservation:
        """Persist an observation to disk for a goal."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")
        if not isinstance(observation, GoalObservation):
            raise TypeError("observation must be a GoalObservation instance.")

        clean_id = goal_id.strip()
        obs_path = self._obs_path(clean_id)

        obs_list: list[dict[str, Any]] = []
        if obs_path.exists():
            try:
                with open(obs_path, "r", encoding="utf-8") as f:
                    obs_list = json.load(f)
            except Exception as e:
                logger.warning("Error reading observations for goal '%s': %s", clean_id, e)
                obs_list = []

        obs_payload = serialize_goal_observation(observation)
        obs_list.append(obs_payload)

        # Enforce maximum observation ring buffer
        if len(obs_list) > self.max_observations_per_goal:
            obs_list = obs_list[-self.max_observations_per_goal:]

        self._write_json_atomic(obs_path, obs_list)
        return copy.deepcopy(observation)

    def get_observations(self, goal_id: str) -> list[GoalObservation]:
        """Retrieve all observations for a goal from disk."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")

        clean_id = goal_id.strip()
        obs_path = self._obs_path(clean_id)
        if not obs_path.exists():
            return []

        with open(obs_path, "r", encoding="utf-8") as f:
            raw_list = json.load(f)

        return [deserialize_goal_observation(item) for item in raw_list if isinstance(item, dict)]

    def clear_observations(self, goal_id: str) -> None:
        """Clear all observations for a goal on disk."""
        if not self.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")

        clean_id = goal_id.strip()
        obs_path = self._obs_path(clean_id)
        if obs_path.exists():
            self._write_json_atomic(obs_path, [])

    def get_subgoals(self, parent_goal_id: str) -> list[Goal]:
        """Retrieve all subgoals of a specific parent goal from disk."""
        return self.list_goals(parent_goal_id=parent_goal_id)

    def get_root_goals(self) -> list[Goal]:
        """Retrieve all top-level root goals from disk."""
        all_goals = self.list_goals()
        return [g for g in all_goals if g.parent_goal_id is None]
