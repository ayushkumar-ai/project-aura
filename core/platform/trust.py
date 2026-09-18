"""M58 — Device Trust, Authority Boundaries & Human Approval Validation.

Enforces device lifecycle trust checks, capability authorization boundaries,
M48 Human Approval validation for High/Critical risk operations, and observation wrapping.
"""

from __future__ import annotations

import logging
from typing import Any

from core.platform.types import (
    CapabilityAuthStatus,
    CapabilityRiskLevel,
    DeviceCapabilityRecord,
    DeviceRecord,
    DeviceTrustState,
)

logger = logging.getLogger("aura.platform.trust")


class DeviceTrustValidator:
    """Validates device and capability trust states prior to command dispatch."""

    @staticmethod
    def validate_device_trust(device: DeviceRecord) -> None:
        """Enforce Invariant M58-F05 & M58-F06 (Device Trust & Revocation)."""
        if device.trust_state == DeviceTrustState.REVOKED:
            raise PermissionError(f"Device '{device.device_id}' is REVOKED and cannot execute operations.")
        if device.trust_state == DeviceTrustState.SUSPENDED:
            raise PermissionError(f"Device '{device.device_id}' is SUSPENDED.")
        if device.trust_state in (DeviceTrustState.UNREGISTERED, DeviceTrustState.PENDING_VERIFICATION):
            raise PermissionError(f"Device '{device.device_id}' has not been verified or authorized (state: {device.trust_state.value}).")

    @staticmethod
    def validate_capability_authorization(capability: DeviceCapabilityRecord) -> None:
        """Enforce Invariant M58-F04 (Capability Authorization)."""
        if capability.auth_status != CapabilityAuthStatus.AUTHORIZED:
            raise PermissionError(
                f"Capability '{capability.name}' on device '{capability.device_id}' is not authorized (status: {capability.auth_status.value})."
            )

    @staticmethod
    def validate_approval_requirement(
        capability: DeviceCapabilityRecord,
        approval_token: str | None = None,
        approval_engine: Any | None = None,
        tenant_id: str = "default",
    ) -> bool:
        """Enforce Invariant M58-F08 (Human Approval Requirement for High/Critical Risk)."""
        if capability.risk_level in (CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL) or capability.requires_approval:
            if not approval_token:
                raise PermissionError(
                    f"Capability '{capability.name}' is classified as {capability.risk_level.value.upper()} risk "
                    f"and requires a valid M48 human approval token."
                )
            if approval_engine:
                # Validate approval token against M48 approval engine
                is_valid = approval_engine.validate_token(
                    token=approval_token,
                    tenant_id=tenant_id,
                    action_type=f"platform:{capability.name}",
                )
                if not is_valid:
                    raise PermissionError(f"Invalid, expired, or unapproved M48 approval token for capability '{capability.name}'.")
        return True

    @staticmethod
    def wrap_device_observation(
        raw_output: dict[str, Any],
        device_id: str,
        capability_name: str,
        provenance: str = "DEVICE_OBSERVED",
    ) -> dict[str, Any]:
        """Wrap device execution output with strict observation provenance (Invariant M58-F21)."""
        return {
            "device_id": device_id,
            "capability_name": capability_name,
            "provenance": provenance,
            "trust_level": "passive_observation",
            "data": raw_output,
        }
