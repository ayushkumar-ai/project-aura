"""M58 — Platform Capability Registry & Resolution.

Maintains deterministic capability definitions, risk classifications, and schema constraints.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from core.platform.types import CapabilityRiskLevel

logger = logging.getLogger("aura.platform.registry")


@dataclass
class PlatformCapabilityDefinition:
    """Specification of a supported platform capability."""
    name: str
    title: str
    description: str
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    requires_approval: bool = False
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self):
        if isinstance(self.risk_level, str):
            self.risk_level = CapabilityRiskLevel(self.risk_level)
        if self.risk_level in (CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL):
            self.requires_approval = True


class PlatformCapabilityRegistry:
    """Thread-safe registry of standardized platform capabilities."""

    def __init__(self, register_defaults: bool = True):
        self._lock = threading.RLock()
        self._capabilities: dict[str, PlatformCapabilityDefinition] = {}
        if register_defaults:
            self._register_defaults()

    def _register_defaults(self) -> None:
        # LOW RISK
        self.register(
            PlatformCapabilityDefinition(
                name="get_system_info",
                title="System Information",
                description="Queries OS name, version, architecture, CPU, and memory summary.",
                risk_level=CapabilityRiskLevel.LOW,
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="get_clock",
                title="System Clock & Timezone",
                description="Retrieves current system time, UTC epoch, and timezone offset.",
                risk_level=CapabilityRiskLevel.LOW,
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="get_battery_status",
                title="Battery Status",
                description="Reads battery percentage and AC power charging state.",
                risk_level=CapabilityRiskLevel.LOW,
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="get_network_status",
                title="Network Status",
                description="Checks network connectivity state and hostname.",
                risk_level=CapabilityRiskLevel.LOW,
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="read_sandboxed_file",
                title="Read Sandboxed File",
                description="Reads UTF-8 or binary contents from tenant-sandboxed file path.",
                risk_level=CapabilityRiskLevel.LOW,
                parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="list_sandboxed_directory",
                title="List Sandboxed Directory",
                description="Lists files and subdirectories in tenant-sandboxed folder.",
                risk_level=CapabilityRiskLevel.LOW,
                parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        )

        # MEDIUM RISK
        self.register(
            PlatformCapabilityDefinition(
                name="write_sandboxed_file",
                title="Write Sandboxed File",
                description="Writes content to a file inside the tenant's sandbox.",
                risk_level=CapabilityRiskLevel.MEDIUM,
                parameters_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="send_notification",
                title="Desktop Notification",
                description="Displays a visual desktop toast notification to the user.",
                risk_level=CapabilityRiskLevel.MEDIUM,
                parameters_schema={"type": "object", "properties": {"title": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]},
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="clipboard_read",
                title="Read Clipboard",
                description="Reads text from the desktop clipboard.",
                risk_level=CapabilityRiskLevel.MEDIUM,
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="clipboard_write",
                title="Write Clipboard",
                description="Copies text into the desktop clipboard.",
                risk_level=CapabilityRiskLevel.MEDIUM,
                parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            )
        )
        self.register(
            PlatformCapabilityDefinition(
                name="take_screenshot",
                title="Capture Screen",
                description="Captures current desktop screen buffer for multimodal analysis.",
                risk_level=CapabilityRiskLevel.MEDIUM,
            )
        )

        # HIGH RISK
        self.register(
            PlatformCapabilityDefinition(
                name="delete_sandboxed_file",
                title="Delete Sandboxed File",
                description="Deletes a file from the tenant sandbox (requires M48 human approval).",
                risk_level=CapabilityRiskLevel.HIGH,
                requires_approval=True,
                parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            )
        )

    def register(self, definition: PlatformCapabilityDefinition) -> None:
        with self._lock:
            self._capabilities[definition.name] = definition

    def get(self, name: str) -> PlatformCapabilityDefinition | None:
        with self._lock:
            return self._capabilities.get(name)

    def list_all(self) -> list[PlatformCapabilityDefinition]:
        with self._lock:
            return sorted(self._capabilities.values(), key=lambda c: c.name)

    def resolve(self, name: str) -> PlatformCapabilityDefinition:
        """Resolve capability definition deterministically or fail closed (Invariant M58-F32)."""
        cap = self.get(name)
        if not cap or not cap.enabled:
            raise ValueError(f"Capability '{name}' is not registered or disabled.")
        return cap
