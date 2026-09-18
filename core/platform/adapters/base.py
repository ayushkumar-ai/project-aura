"""M58 — Base Platform Adapter Interface.

Defines the typed execution contract for OS and device adapters.
"""

from __future__ import annotations

import abc
from typing import Any

from core.platform.types import ExecutionMode, PlatformType


class BasePlatformAdapter(abc.ABC):
    """Abstract interface for platform and device execution adapters."""

    @property
    @abc.abstractmethod
    def platform_type(self) -> PlatformType:
        """Operating system or environment type handled by this adapter."""
        ...

    @property
    @abc.abstractmethod
    def execution_mode(self) -> ExecutionMode:
        """Verifiable provenance mode (REAL, SIMULATED, MOCK)."""
        ...

    @abc.abstractmethod
    def execute(
        self,
        tenant_id: str,
        capability_name: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a typed, sandboxed capability on the target environment.
        
        Must return normalized result dictionary or raise an exception.
        """
        ...

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if target platform/device runtime is currently reachable."""
        ...
