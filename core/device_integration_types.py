"""M38 — Device & Environment Integration Types.

Defines schemas for device descriptors, capability contracts, action requests,
action results, and security audit records.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DeviceType(str, Enum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    IOT_SENSOR = "iot_sensor"
    SMART_HOME = "smart_home"
    LOCAL_ENVIRONMENT = "local_environment"


class DeviceCapability(str, Enum):
    SEND_NOTIFICATION = "send_notification"
    READ_TELEMETRY = "read_telemetry"
    CONTROL_SWITCH = "control_switch"
    ADJUST_LEVEL = "adjust_level"
    CLIPBOARD_ACCESS = "clipboard_access"
    EXECUTE_ACTION = "execute_action"


@dataclass
class DeviceDescriptor:
    device_id: str
    name: str
    device_type: DeviceType
    capabilities: list[DeviceCapability] = field(default_factory=list)
    is_online: bool = True
    authorized_users: list[str] = field(default_factory=lambda: ["user", "admin"])
    last_seen: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type.value,
            "capabilities": [c.value for c in self.capabilities],
            "is_online": self.is_online,
            "authorized_users": self.authorized_users,
            "last_seen": self.last_seen,
            "metadata": self.metadata,
        }


@dataclass
class DeviceActionRequest:
    action_id: str
    device_id: str
    capability: DeviceCapability
    parameters: dict[str, Any] = field(default_factory=dict)
    caller: str = "agent"
    user_id: str = "user"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["capability"] = self.capability.value
        return d


@dataclass
class DeviceActionResult:
    action_id: str
    device_id: str
    capability: str
    success: bool
    output: Any = None
    error: str = ""
    duration_seconds: float = 0.0
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceAuditRecord:
    timestamp: float
    action_id: str
    device_id: str
    capability: str
    caller: str
    success: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
