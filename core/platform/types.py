"""M58 — Real-World Device, Desktop OS & Platform Integration Types.

Defines domain models for devices, platform types, trust states, capability risk tiers,
authorization statuses, execution requests/results, audit events, and limits.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.cognitive_memory.types import scrub_sensitive_content


class PlatformType(str, Enum):
    """Supported operating system and platform environments."""
    WINDOWS = "windows"
    WSL = "wsl"
    LINUX = "linux"
    MACOS = "macos"
    ANDROID = "android"
    GENERIC = "generic"


class DeviceType(str, Enum):
    """Classification of target execution environments."""
    DESKTOP = "desktop"
    MOBILE = "mobile"
    SERVER = "server"
    IOT_DEVICE = "iot_device"
    VIRTUAL_ENVIRONMENT = "virtual_environment"


class DeviceTrustState(str, Enum):
    """Lifecycle trust state machine for registered devices."""
    UNREGISTERED = "unregistered"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    AUTHORIZED = "authorized"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class CapabilityRiskLevel(str, Enum):
    """Explicit risk classification for platform capabilities."""
    LOW = "low"            # Read-only telemetry, clock, battery, system info
    MEDIUM = "medium"      # Sandboxed file read/write, screenshot, notifications
    HIGH = "high"          # File deletion, service restart, shell tool, outbound messaging
    CRITICAL = "critical"  # Credential access, security settings, irreversible destructive actions


class CapabilityAuthStatus(str, Enum):
    """Authorization status for a device capability."""
    DECLARED = "declared"
    VERIFIED = "verified"
    AUTHORIZED = "authorized"
    DISABLED = "disabled"
    REVOKED = "revoked"


class ExecutionMode(str, Enum):
    """Verifiable execution provenance mode."""
    REAL = "real"
    SIMULATED = "simulated"
    MOCK = "mock"
    UNAVAILABLE = "unavailable"


class ExecutionStatus(str, Enum):
    """Execution state of a platform operation."""
    REQUESTED = "requested"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class PlatformLimitsConfig:
    """Configurable resource and security envelopes for platform interactions."""
    max_execution_timeout_seconds: float = 30.0
    max_output_size_bytes: int = 1 * 1024 * 1024  # 1 MB
    max_retries: int = 3
    sandbox_root_dir: str = "data/platform_sandbox"
    require_approval_for_high_risk: bool = True
    require_approval_for_critical_risk: bool = True


@dataclass
class DeviceRecord:
    """Authoritative domain record for a registered device."""
    device_id: str = field(default_factory=lambda: f"dev_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    name: str = "Desktop Workstation"
    device_type: DeviceType = DeviceType.DESKTOP
    platform: PlatformType = PlatformType.WINDOWS
    platform_version: str = ""
    trust_state: DeviceTrustState = DeviceTrustState.PENDING_VERIFICATION
    hostname: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.device_type, str):
            self.device_type = DeviceType(self.device_type)
        if isinstance(self.platform, str):
            self.platform = PlatformType(self.platform)
        if isinstance(self.trust_state, str):
            self.trust_state = DeviceTrustState(self.trust_state)
        if isinstance(self.metadata, dict):
            cleaned = {}
            for k, v in self.metadata.items():
                if isinstance(v, str):
                    cleaned[k] = scrub_sensitive_content(v)
                elif not callable(v):
                    cleaned[k] = v
            self.metadata = cleaned

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "device_type": self.device_type.value if isinstance(self.device_type, Enum) else str(self.device_type),
            "platform": self.platform.value if isinstance(self.platform, Enum) else str(self.platform),
            "platform_version": self.platform_version,
            "trust_state": self.trust_state.value if isinstance(self.trust_state, Enum) else str(self.trust_state),
            "hostname": self.hostname,
            "metadata": dict(self.metadata),
            "registered_at": self.registered_at,
            "last_seen_at": self.last_seen_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceRecord:
        return cls(
            device_id=data.get("device_id", f"dev_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            name=data.get("name", "Desktop Workstation"),
            device_type=data.get("device_type", DeviceType.DESKTOP),
            platform=data.get("platform", PlatformType.WINDOWS),
            platform_version=data.get("platform_version", ""),
            trust_state=data.get("trust_state", DeviceTrustState.PENDING_VERIFICATION),
            hostname=data.get("hostname", ""),
            metadata=data.get("metadata", {}),
            registered_at=data.get("registered_at", time.time()),
            last_seen_at=data.get("last_seen_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class DeviceCapabilityRecord:
    """Domain record for an individual capability bound to a device."""
    capability_id: str = field(default_factory=lambda: f"cap_{uuid4().hex[:16]}")
    device_id: str = ""
    tenant_id: str = "default"
    name: str = ""
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    auth_status: CapabilityAuthStatus = CapabilityAuthStatus.DECLARED
    description: str = ""
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.risk_level, str):
            self.risk_level = CapabilityRiskLevel(self.risk_level)
        if isinstance(self.auth_status, str):
            self.auth_status = CapabilityAuthStatus(self.auth_status)
        if self.risk_level in (CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL):
            self.requires_approval = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, Enum) else str(self.risk_level),
            "auth_status": self.auth_status.value if isinstance(self.auth_status, Enum) else str(self.auth_status),
            "description": self.description,
            "parameters_schema": dict(self.parameters_schema),
            "requires_approval": self.requires_approval,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceCapabilityRecord:
        return cls(
            capability_id=data.get("capability_id", f"cap_{uuid4().hex[:16]}"),
            device_id=data.get("device_id", ""),
            tenant_id=data.get("tenant_id", "default"),
            name=data.get("name", ""),
            risk_level=data.get("risk_level", CapabilityRiskLevel.LOW),
            auth_status=data.get("auth_status", CapabilityAuthStatus.DECLARED),
            description=data.get("description", ""),
            parameters_schema=data.get("parameters_schema", {}),
            requires_approval=data.get("requires_approval", False),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class DeviceSessionRecord:
    """Session record tracking an active device connection."""
    session_id: str = field(default_factory=lambda: f"ses_{uuid4().hex[:16]}")
    device_id: str = ""
    tenant_id: str = "default"
    status: str = "active"
    ip_address: str = ""
    started_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    last_heartbeat_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "ip_address": self.ip_address,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
        }


@dataclass
class DeviceExecutionRecord:
    """Historical execution record of a platform/device operation."""
    execution_id: str = field(default_factory=lambda: f"exec_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    device_id: str = ""
    capability_name: str = ""
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    status: ExecutionStatus = ExecutionStatus.REQUESTED
    execution_mode: ExecutionMode = ExecutionMode.REAL
    idempotency_key: str | None = None
    approval_token: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error_detail: str | None = None
    duration_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def __post_init__(self):
        if isinstance(self.risk_level, str):
            self.risk_level = CapabilityRiskLevel(self.risk_level)
        if isinstance(self.status, str):
            self.status = ExecutionStatus(self.status)
        if isinstance(self.execution_mode, str):
            self.execution_mode = ExecutionMode(self.execution_mode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "capability_name": self.capability_name,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, Enum) else str(self.risk_level),
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "execution_mode": self.execution_mode.value if isinstance(self.execution_mode, Enum) else str(self.execution_mode),
            "idempotency_key": self.idempotency_key,
            "approval_token": self.approval_token,
            "parameters": dict(self.parameters),
            "result": dict(self.result),
            "error_detail": self.error_detail,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceExecutionRecord:
        return cls(
            execution_id=data.get("execution_id", f"exec_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            device_id=data.get("device_id", ""),
            capability_name=data.get("capability_name", ""),
            risk_level=data.get("risk_level", CapabilityRiskLevel.LOW),
            status=data.get("status", ExecutionStatus.REQUESTED),
            execution_mode=data.get("execution_mode", ExecutionMode.REAL),
            idempotency_key=data.get("idempotency_key"),
            approval_token=data.get("approval_token"),
            parameters=data.get("parameters", {}),
            result=data.get("result", {}),
            error_detail=data.get("error_detail"),
            duration_ms=data.get("duration_ms", 0.0),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
        )


@dataclass
class DeviceAuditEvent:
    """Security and compliance audit record for platform events."""
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    device_id: str = ""
    action: str = ""
    principal_id: str = ""
    event_type: str = "execution"
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    details: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "action": self.action,
            "principal_id": self.principal_id,
            "event_type": self.event_type,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, Enum) else str(self.risk_level),
            "details": dict(self.details),
            "created_at": self.created_at,
        }
