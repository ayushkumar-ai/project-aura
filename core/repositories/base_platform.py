"""M58 — Base Platform & Device Repository Abstract Interface.

Defines persistence contracts for devices, capabilities, sessions, execution records,
audit events, and tenant lifecycle purge operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.platform.types import (
    CapabilityAuthStatus,
    DeviceAuditEvent,
    DeviceCapabilityRecord,
    DeviceExecutionRecord,
    DeviceRecord,
    DeviceSessionRecord,
    DeviceTrustState,
)


class BasePlatformRepository(ABC):
    """Abstract repository for Project AURA platform and device persistence."""

    # 1. Device Records
    @abstractmethod
    def save_device(self, device: DeviceRecord) -> DeviceRecord:
        """Create or update a device record."""
        ...

    @abstractmethod
    def get_device(self, device_id: str, tenant_id: str) -> DeviceRecord | None:
        """Fetch a single device by ID scoped strictly to a tenant."""
        ...

    @abstractmethod
    def list_devices(self, tenant_id: str) -> list[DeviceRecord]:
        """List all registered devices for a tenant."""
        ...

    @abstractmethod
    def update_device_trust(self, device_id: str, tenant_id: str, trust_state: DeviceTrustState) -> DeviceRecord | None:
        """Update device trust state (e.g. AUTHORIZED, SUSPENDED, REVOKED)."""
        ...

    @abstractmethod
    def delete_device(self, device_id: str, tenant_id: str) -> bool:
        """Permanently delete a device and its child records."""
        ...

    # 2. Capabilities
    @abstractmethod
    def save_capability(self, capability: DeviceCapabilityRecord) -> DeviceCapabilityRecord:
        """Register or update a capability for a device."""
        ...

    @abstractmethod
    def get_capability(self, capability_id: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        """Fetch capability by ID and tenant."""
        ...

    @abstractmethod
    def get_capability_by_name(self, device_id: str, capability_name: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        """Fetch capability by device and name for a tenant."""
        ...

    @abstractmethod
    def list_capabilities(self, device_id: str, tenant_id: str) -> list[DeviceCapabilityRecord]:
        """List all declared capabilities for a device."""
        ...

    @abstractmethod
    def update_capability_auth(self, capability_id: str, tenant_id: str, auth_status: CapabilityAuthStatus) -> DeviceCapabilityRecord | None:
        """Update authorization status of a capability."""
        ...

    # 3. Sessions
    @abstractmethod
    def save_session(self, session: DeviceSessionRecord) -> DeviceSessionRecord:
        """Record or update a device session."""
        ...

    @abstractmethod
    def get_session(self, session_id: str, tenant_id: str) -> DeviceSessionRecord | None:
        """Fetch a device session by ID."""
        ...

    # 4. Executions & Idempotency
    @abstractmethod
    def save_execution(self, execution: DeviceExecutionRecord) -> DeviceExecutionRecord:
        """Record or update a platform execution."""
        ...

    @abstractmethod
    def get_execution(self, execution_id: str, tenant_id: str) -> DeviceExecutionRecord | None:
        """Fetch execution record by ID and tenant."""
        ...

    @abstractmethod
    def get_execution_by_idempotency(self, tenant_id: str, idempotency_key: str) -> DeviceExecutionRecord | None:
        """Retrieve execution by idempotency key for replay prevention."""
        ...

    @abstractmethod
    def list_executions(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceExecutionRecord]:
        """List recent execution records for a device."""
        ...

    # 5. Audit Events
    @abstractmethod
    def save_audit_event(self, event: DeviceAuditEvent) -> DeviceAuditEvent:
        """Persist an immutable audit record."""
        ...

    @abstractmethod
    def list_audit_events(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceAuditEvent]:
        """List audit events for a device."""
        ...

    # 6. Tenant Purge (GDPR)
    @abstractmethod
    def purge_tenant_data(self, tenant_id: str) -> int:
        """Permanently delete all devices, capabilities, executions, and audits for a tenant."""
        ...
