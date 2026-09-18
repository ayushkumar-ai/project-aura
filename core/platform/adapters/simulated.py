"""M58 — Simulated Platform Adapter for Testing & Ephemeral Environments.

Provides deterministic responses for test suites, clearly tagged as ExecutionMode.SIMULATED.
"""

from __future__ import annotations

import time
from typing import Any

from core.platform.adapters.base import BasePlatformAdapter
from core.platform.types import ExecutionMode, PlatformType


class SimulatedPlatformAdapter(BasePlatformAdapter):
    """Explicit test/mock adapter that tags all execution results with SIMULATED provenance."""

    def __init__(self, platform_type: PlatformType = PlatformType.GENERIC):
        self._platform_type = platform_type
        self._memory_store: dict[str, dict[str, str]] = {}
        self._available = True

    @property
    def platform_type(self) -> PlatformType:
        return self._platform_type

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.SIMULATED

    def is_available(self) -> bool:
        return self._available

    def set_available(self, available: bool) -> None:
        self._available = available

    def execute(
        self,
        tenant_id: str,
        capability_name: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._available:
            raise ConnectionError("Simulated device is currently offline.")

        if capability_name == "get_system_info":
            return {
                "os_name": "SimulatedOS",
                "os_release": "1.0.0",
                "architecture": "x86_64",
                "simulated": True,
            }
        elif capability_name == "get_clock":
            return {
                "utc_timestamp": time.time(),
                "timezone": "UTC",
                "simulated": True,
            }
        elif capability_name == "read_sandboxed_file":
            rel_path = parameters.get("path", "")
            tenant_files = self._memory_store.get(tenant_id, {})
            if rel_path not in tenant_files:
                raise FileNotFoundError(f"Simulated file '{rel_path}' not found.")
            return {
                "path": rel_path,
                "content": tenant_files[rel_path],
                "simulated": True,
            }
        elif capability_name == "write_sandboxed_file":
            rel_path = parameters.get("path", "")
            content = parameters.get("content", "")
            if tenant_id not in self._memory_store:
                self._memory_store[tenant_id] = {}
            self._memory_store[tenant_id][rel_path] = content
            return {
                "path": rel_path,
                "bytes_written": len(content),
                "status": "written",
                "simulated": True,
            }
        elif capability_name == "delete_sandboxed_file":
            rel_path = parameters.get("path", "")
            if tenant_id in self._memory_store and rel_path in self._memory_store[tenant_id]:
                del self._memory_store[tenant_id][rel_path]
                return {"path": rel_path, "status": "deleted", "simulated": True}
            raise FileNotFoundError(f"Simulated file '{rel_path}' not found for deletion.")

        return {
            "capability": capability_name,
            "status": "executed",
            "parameters": parameters,
            "simulated": True,
        }
