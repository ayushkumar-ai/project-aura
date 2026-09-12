"""M34 — Tool & Action Ecosystem Types.

Defines standardized tool specifications, permission tiers, execution requests,
execution results, and tool audit records.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum, Enum
from typing import Any


class ToolPermissionTier(IntEnum):
    READ_ONLY = 1
    SAFE_WRITE = 2
    RESTRICTED = 3
    CRITICAL = 4


@dataclass
class ToolParameterSchema:
    name: str
    type_str: str = "string"  # string, int, float, boolean, object, array
    description: str = ""
    required: bool = True
    default: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolSpec:
    name: str
    description: str
    permission_tier: ToolPermissionTier = ToolPermissionTier.READ_ONLY
    parameters: list[ToolParameterSchema] = field(default_factory=list)
    timeout_seconds: float = 10.0
    is_deterministic: bool = True
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission_tier": int(self.permission_tier),
            "permission_tier_name": self.permission_tier.name,
            "parameters": [p.to_dict() for p in self.parameters],
            "timeout_seconds": self.timeout_seconds,
            "is_deterministic": self.is_deterministic,
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class ToolExecutionRequest:
    tool_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    caller_role: str = "agent"
    user_id: str = "system"
    timeout_seconds: float | None = None
    execution_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolExecutionResult:
    execution_id: str
    tool_name: str
    success: bool
    output: Any = None
    error: str = ""
    duration_seconds: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolAuditRecord:
    timestamp: float
    execution_id: str
    tool_name: str
    caller: str
    permission_tier: int
    success: bool
    duration_seconds: float
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
