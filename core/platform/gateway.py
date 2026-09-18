"""M58 — Platform Integration Gateway.

Coordinates device registration, capability authorization, trust lifecycle,
policy evaluation, M48 human approval gating, typed adapter dispatch,
idempotency, telemetry, and audit logging.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.metrics import get_metrics_registry
from core.platform.adapters.base import BasePlatformAdapter
from core.platform.adapters.desktop import DesktopOSAdapter
from core.platform.registry import PlatformCapabilityRegistry
from core.platform.security import PlatformSecurityManager
from core.platform.trust import DeviceTrustValidator
from core.platform.types import (
    CapabilityAuthStatus,
    CapabilityRiskLevel,
    DeviceAuditEvent,
    DeviceCapabilityRecord,
    DeviceExecutionRecord,
    DeviceRecord,
    DeviceTrustState,
    DeviceType,
    ExecutionMode,
    ExecutionStatus,
    PlatformLimitsConfig,
    PlatformType,
)

if TYPE_CHECKING:
    from core.repositories.base_platform import BasePlatformRepository

logger = logging.getLogger("aura.platform.gateway")


class PlatformIntegrationGateway:
    """Production gateway coordinating secure platform and device interactions."""

    def __init__(
        self,
        repository: BasePlatformRepository | Any,

        policy_engine: Any | None = None,
        approval_engine: Any | None = None,
        capability_registry: PlatformCapabilityRegistry | None = None,
        security_manager: PlatformSecurityManager | None = None,
        limits: PlatformLimitsConfig | None = None,
        adapter_override: BasePlatformAdapter | None = None,
    ):
        self.repository = repository
        self.policy_engine = policy_engine
        self.approval_engine = approval_engine
        self.registry = capability_registry or PlatformCapabilityRegistry()
        self.security = security_manager or PlatformSecurityManager()
        self.limits = limits or PlatformLimitsConfig()
        self.default_adapter = adapter_override or DesktopOSAdapter(self.security)
        self._custom_adapters: dict[str, BasePlatformAdapter] = {}

    def register_adapter(self, device_type: str, adapter: BasePlatformAdapter) -> None:
        """Register a specialized adapter for a device type or platform."""
        self._custom_adapters[device_type] = adapter

    def get_adapter(self, device: DeviceRecord) -> BasePlatformAdapter:
        """Resolve adapter for target device."""
        dev_type_str = device.device_type.value if isinstance(device.device_type, DeviceType) else str(device.device_type)
        return self._custom_adapters.get(dev_type_str, self.default_adapter)

    # 1. Device Registration & Lifecycle
    def register_device(
        self,
        tenant_id: str,
        name: str,
        device_type: DeviceType | str = DeviceType.DESKTOP,
        platform: PlatformType | str = PlatformType.WINDOWS,
        platform_version: str = "",
        hostname: str = "",
        metadata: dict[str, Any] | None = None,
        auto_authorize: bool = False,
    ) -> DeviceRecord:
        """Register a new device for an authenticated tenant (Invariant M58-F01 & M58-F03)."""
        device_id = f"dev_{uuid4().hex[:16]}"
        initial_trust = DeviceTrustState.AUTHORIZED if auto_authorize else DeviceTrustState.PENDING_VERIFICATION

        device = DeviceRecord(
            device_id=device_id,
            tenant_id=tenant_id,
            name=name,
            device_type=DeviceType(device_type) if isinstance(device_type, str) else device_type,
            platform=PlatformType(platform) if isinstance(platform, str) else platform,
            platform_version=platform_version,
            trust_state=initial_trust,
            hostname=hostname,
            metadata=metadata or {},
        )
        saved_device = self.repository.save_device(device)

        # Register standard declared capabilities from capability registry
        for cap_def in self.registry.list_all():
            initial_cap_auth = CapabilityAuthStatus.AUTHORIZED if auto_authorize else CapabilityAuthStatus.DECLARED
            cap_record = DeviceCapabilityRecord(
                capability_id=f"cap_{uuid4().hex[:16]}",
                device_id=device_id,
                tenant_id=tenant_id,
                name=cap_def.name,
                risk_level=cap_def.risk_level,
                auth_status=initial_cap_auth,
                description=cap_def.description,
                parameters_schema=cap_def.parameters_schema,
                requires_approval=cap_def.requires_approval,
            )
            self.repository.save_capability(cap_record)

        # Audit Event
        self.repository.save_audit_event(
            DeviceAuditEvent(
                tenant_id=tenant_id,
                device_id=device_id,
                action="device_registered",
                principal_id=tenant_id,
                event_type="registration",
                risk_level=CapabilityRiskLevel.LOW,
                details={"name": name, "device_type": device.device_type.value, "platform": device.platform.value},
            )
        )
        return saved_device

    def update_device_trust(
        self,
        tenant_id: str,
        device_id: str,
        trust_state: DeviceTrustState | str,
    ) -> DeviceRecord:
        """Transition device trust state (e.g. AUTHORIZE, SUSPEND, REVOKE)."""
        state_enum = DeviceTrustState(trust_state) if isinstance(trust_state, str) else trust_state
        device = self.repository.get_device(device_id, tenant_id)
        if not device:
            raise ValueError(f"Device '{device_id}' not found for tenant '{tenant_id}'.")

        updated = self.repository.update_device_trust(device_id, tenant_id, state_enum)
        if not updated:
            raise RuntimeError(f"Failed to update trust state for device '{device_id}'.")

        self.repository.save_audit_event(
            DeviceAuditEvent(
                tenant_id=tenant_id,
                device_id=device_id,
                action="device_trust_updated",
                principal_id=tenant_id,
                event_type="trust_transition",
                risk_level=CapabilityRiskLevel.HIGH if state_enum == DeviceTrustState.REVOKED else CapabilityRiskLevel.LOW,
                details={"previous_state": device.trust_state.value, "new_state": state_enum.value},
            )
        )
        return updated

    def authorize_capability(
        self,
        tenant_id: str,
        device_id: str,
        capability_name: str,
        auth_status: CapabilityAuthStatus | str = CapabilityAuthStatus.AUTHORIZED,
    ) -> DeviceCapabilityRecord:
        """Grant or revoke authorization for an individual capability (Invariant M58-F04)."""
        status_enum = CapabilityAuthStatus(auth_status) if isinstance(auth_status, str) else auth_status
        cap = self.repository.get_capability_by_name(device_id, capability_name, tenant_id)
        if not cap:
            raise ValueError(f"Capability '{capability_name}' not found on device '{device_id}'.")

        updated = self.repository.update_capability_auth(cap.capability_id, tenant_id, status_enum)
        if not updated:
            raise RuntimeError(f"Failed to update capability '{capability_name}'.")

        self.repository.save_audit_event(
            DeviceAuditEvent(
                tenant_id=tenant_id,
                device_id=device_id,
                action="capability_auth_updated",
                principal_id=tenant_id,
                event_type="authorization",
                risk_level=cap.risk_level,
                details={"capability_name": capability_name, "status": status_enum.value},
            )
        )
        return updated

    # 2. Capability Execution Dispatch
    def execute_action(
        self,
        tenant_id: str,
        device_id: str,
        capability_name: str,
        parameters: dict[str, Any] | None = None,
        approval_token: str | None = None,
        idempotency_key: str | None = None,
        principal_role: str = "user",
    ) -> DeviceExecutionRecord:
        """Coordinate safe, policy-governed, approval-checked capability execution."""
        start_time = time.time()
        parameters = parameters or {}

        # 1. Verify Device Ownership & Identity (Invariant M58-F01, M58-F02, M58-F03)
        device = self.repository.get_device(device_id, tenant_id)
        if not device:
            raise ValueError(f"Device '{device_id}' not found for tenant '{tenant_id}'.")

        # 2. Verify Device Trust (Invariant M58-F05, M58-F06)
        DeviceTrustValidator.validate_device_trust(device)

        # 3. Check Idempotency (Invariant M58-F18)
        if idempotency_key:
            existing_exec = self.repository.get_execution_by_idempotency(tenant_id, idempotency_key)
            if existing_exec and existing_exec.status == ExecutionStatus.SUCCEEDED:
                return existing_exec

        # 4. Resolve & Verify Capability (Invariant M58-F04, M58-F32)
        cap = self.repository.get_capability_by_name(device_id, capability_name, tenant_id)
        if not cap:
            raise ValueError(f"Capability '{capability_name}' is not registered on device '{device_id}'.")
        DeviceTrustValidator.validate_capability_authorization(cap)

        # 5. Policy Engine Evaluation (Invariant M58-F07)
        if self.policy_engine:
            policy_allowed = self.policy_engine.evaluate(
                action=f"device:{capability_name}",
                tenant_id=tenant_id,
                context={"device_id": device_id, "risk_level": cap.risk_level.value},
            )
            if not policy_allowed:
                raise PermissionError(f"Policy denied execution of capability '{capability_name}'.")

        # 6. M48 Human Approval Check for High/Critical Risk (Invariant M58-F08)
        DeviceTrustValidator.validate_approval_requirement(
            capability=cap,
            approval_token=approval_token,
            approval_engine=self.approval_engine,
            tenant_id=tenant_id,
        )

        # 7. Create Execution Record
        adapter = self.get_adapter(device)
        execution_id = f"exec_{uuid4().hex[:16]}"
        record = DeviceExecutionRecord(
            execution_id=execution_id,
            tenant_id=tenant_id,
            device_id=device_id,
            capability_name=capability_name,
            risk_level=cap.risk_level,
            status=ExecutionStatus.EXECUTING,
            execution_mode=adapter.execution_mode,
            idempotency_key=idempotency_key,
            approval_token=approval_token,
            parameters=parameters,
            created_at=time.time(),
        )
        record = self.repository.save_execution(record)

        # 8. Dispatch to Adapter
        raw_result: dict[str, Any] = {}
        error_msg: str | None = None
        status = ExecutionStatus.SUCCEEDED

        try:
            raw_result = adapter.execute(tenant_id=tenant_id, capability_name=capability_name, parameters=parameters)
            raw_result = self.security.scrub_output(raw_result)
        except Exception as e:
            logger.warning(f"Execution failed for {capability_name} on {device_id}: {e}")
            status = ExecutionStatus.FAILED
            error_msg = str(e)

        duration_ms = (time.time() - start_time) * 1000.0

        # 9. Format Normalized Output (Invariant M58-F21)
        wrapped_output = DeviceTrustValidator.wrap_device_observation(
            raw_output=raw_result,
            device_id=device_id,
            capability_name=capability_name,
        ) if status == ExecutionStatus.SUCCEEDED else {}

        # 10. Finalize Execution Record
        record.status = status
        record.result = wrapped_output
        record.error_detail = error_msg
        record.duration_ms = duration_ms
        record.completed_at = time.time()
        record = self.repository.save_execution(record)

        # 11. Audit Event & Telemetry
        self.repository.save_audit_event(
            DeviceAuditEvent(
                tenant_id=tenant_id,
                device_id=device_id,
                action=f"execute_{capability_name}",
                principal_id=tenant_id,
                event_type="execution",
                risk_level=cap.risk_level,
                details={
                    "status": status.value,
                    "duration_ms": duration_ms,
                    "execution_mode": adapter.execution_mode.value,
                },
            )
        )

        try:
            metrics = get_metrics_registry()
            metrics.get_counter("aura_device_operations_total").inc(
                labels={
                    "device_type": device.device_type.value,
                    "platform": device.platform.value,
                    "operation": capability_name,
                    "status": status.value,
                }
            )
            metrics.get_histogram("aura_device_duration_seconds").observe(
                duration_ms / 1000.0,
                labels={"operation": capability_name},
            )
        except Exception:
            pass

        if status == ExecutionStatus.FAILED:
            raise RuntimeError(error_msg or f"Execution failed for capability '{capability_name}'.")

        return record
