"""M53 — Tenant-Scoped Read-Only Context Provider for Condition Evaluation."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from core.automations.types import AutomationValidationError

logger = logging.getLogger("aura.automations.context")

# Whitelist of permissible context keys for Tier 2 evaluation
WHITELISTED_CONTEXT_PREFIXES = {
    "system.time",
    "user.preferences",
    "user.tasks.recent",
    "user.memories.count",
    "user.automations.count",
}


class ReadOnlyContextProvider:
    """Provides read-only, strictly tenant-isolated context data for condition evaluation."""

    def __init__(self, repositories: Any | None = None) -> None:
        self.repos = repositories

    def validate_keys(self, keys: list[str]) -> None:
        """Validate that all requested keys match the strict whitelist."""
        for key in keys:
            key_str = key.strip()
            if not any(key_str == p or key_str.startswith(f"{p}.") for p in WHITELISTED_CONTEXT_PREFIXES):
                raise AutomationValidationError(
                    f"Unwhitelisted context key requested: '{key_str}'. Only whitelisted keys are permitted."
                )

    def get_context(self, user_id: str, keys: list[str]) -> dict[str, Any]:
        """Fetch tenant-scoped context data for the requested keys."""
        self.validate_keys(keys)
        context: dict[str, Any] = {}

        now = datetime.datetime.now(datetime.timezone.utc)
        system_time_data = {
            "epoch": now.timestamp(),
            "iso": now.isoformat(),
            "hour": now.hour,
            "minute": now.minute,
            "day_of_week": (now.weekday() + 1) % 7,  # Sunday=0..Saturday=6
            "day": now.day,
            "month": now.month,
            "year": now.year,
        }

        for key in keys:
            key_str = key.strip()
            if key_str == "system.time":
                context["system.time"] = system_time_data
            elif key_str.startswith("system.time."):
                sub = key_str[len("system.time."):]
                context[key_str] = system_time_data.get(sub)
            elif key_str.startswith("user.preferences"):
                prefs = {}
                if self.repos and hasattr(self.repos, "preferences") and self.repos.preferences:
                    try:
                        prefs = self.repos.preferences.get_preferences(user_id) or {}
                    except Exception as e:
                        logger.warning(f"Failed to fetch preferences for user {user_id}: {e}")
                if key_str == "user.preferences":
                    context["user.preferences"] = prefs
                else:
                    sub = key_str[len("user.preferences."):]
                    context[key_str] = prefs.get(sub)
            elif key_str == "user.tasks.recent":
                recent_summary = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "total": 0}
                if self.repos and hasattr(self.repos, "tasks") and self.repos.tasks:
                    try:
                        user_tasks = self.repos.tasks.list_tasks(user_id=user_id, limit=20)
                        recent_summary["total"] = len(user_tasks)
                        for t in user_tasks:
                            st = t.get("status", "pending")
                            if st in recent_summary:
                                recent_summary[st] += 1
                    except Exception as e:
                        logger.warning(f"Failed to fetch recent tasks for user {user_id}: {e}")
                context["user.tasks.recent"] = recent_summary
            elif key_str == "user.memories.count":
                mem_count = 0
                if self.repos and hasattr(self.repos, "memories") and self.repos.memories:
                    try:
                        mems = self.repos.memories.list_memories(user_id=user_id)
                        mem_count = len(mems)
                    except Exception:
                        pass
                context["user.memories.count"] = mem_count
            elif key_str == "user.automations.count":
                auto_count = 0
                if self.repos and hasattr(self.repos, "automations") and self.repos.automations:
                    try:
                        auto_count = self.repos.automations.count_automations(user_id=user_id)
                    except Exception:
                        pass
                context["user.automations.count"] = auto_count

        return context
