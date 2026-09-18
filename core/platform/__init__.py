"""M58 — Real-World Device, Desktop OS & Platform Integration Subsystem."""

from core.platform.adapters.base import BasePlatformAdapter
from core.platform.adapters.desktop import DesktopOSAdapter
from core.platform.adapters.simulated import SimulatedPlatformAdapter
from core.platform.gateway import PlatformIntegrationGateway
from core.platform.integration import (
    PlatformMemoryBridge,
    PlatformMultimodalBridge,
)
from core.platform.registry import (
    PlatformCapabilityDefinition,
    PlatformCapabilityRegistry,
)
from core.platform.security import PlatformSecurityManager
from core.platform.trust import DeviceTrustValidator
from core.platform.types import (
    CapabilityAuthStatus,
    CapabilityRiskLevel,
    DeviceAuditEvent,
    DeviceCapabilityRecord,
    DeviceExecutionRecord,
    DeviceRecord,
    DeviceSessionRecord,
    DeviceTrustState,
    DeviceType,
    ExecutionMode,
    ExecutionStatus,
    PlatformLimitsConfig,
    PlatformType,
)

__all__ = [
    "PlatformType",
    "DeviceType",
    "DeviceTrustState",
    "CapabilityRiskLevel",
    "CapabilityAuthStatus",
    "ExecutionMode",
    "ExecutionStatus",
    "PlatformLimitsConfig",
    "DeviceRecord",
    "DeviceCapabilityRecord",
    "DeviceSessionRecord",
    "DeviceExecutionRecord",
    "DeviceAuditEvent",
    "PlatformSecurityManager",
    "PlatformCapabilityDefinition",
    "PlatformCapabilityRegistry",
    "BasePlatformAdapter",
    "DesktopOSAdapter",
    "SimulatedPlatformAdapter",
    "DeviceTrustValidator",
    "PlatformIntegrationGateway",
    "PlatformMemoryBridge",
    "PlatformMultimodalBridge",
]
