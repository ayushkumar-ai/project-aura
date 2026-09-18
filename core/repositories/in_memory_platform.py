"""M58 — In-Memory Platform & Device Repository Implementation.

Thread-safe in-memory store for unit tests, isolated integration tests, and development mode.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.platform.types import (
    CapabilityAuthStatus,
    DeviceAuditEvent,
    DeviceCapabilityRecord,
    DeviceExecutionRecord,
    DeviceRecord,
    DeviceSessionRecord,
    DeviceTrustState,
)
from core.repositories.base_platform import BasePlatformRepository


class InMemoryPlatformRepository(BasePlatformRepository):
    """Thread-safe in-memory platform repository."""

    def __init__(self):
        self._lock = threading.RLock()
        self._devices: dict[str, dict[str, DeviceRecord]] = {}                   # tenant_id -> {device_id: DeviceRecord}
        self._capabilities: dict[str, dict[str, DeviceCapabilityRecord]] = {}   # tenant_id -> {capability_id: DeviceCapabilityRecord}
        self._sessions: dict[str, dict[str, DeviceSessionRecord]] = {}           # tenant_id -> {session_id: DeviceSessionRecord}
        self._executions: dict[str, dict[str, DeviceExecutionRecord]] = {}       # tenant_id -> {execution_id: DeviceExecutionRecord}
        self._audit_events: dict[str, list[DeviceAuditEvent]] = {}               # tenant_id -> [DeviceAuditEvent]

    # 1. Devices
    def save_device(self, device: DeviceRecord) -> DeviceRecord:
        with self._lock:
            if device.tenant_id not in self._devices:
                self._devices[device.tenant_id] = {}
            device.updated_at = time.time()
            self._devices[device.tenant_id][device.device_id] = device
            return device

    def get_device(self, device_id: str, tenant_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(tenant_id, {}).get(device_id)

    def list_devices(self, tenant_id: str) -> list[DeviceRecord]:
        with self._lock:
            return sorted(self._devices.get(tenant_id, {}).values(), key=lambda d: d.registered_at)

    def update_device_trust(self, device_id: str, tenant_id: str, trust_state: DeviceTrustState) -> DeviceRecord | None:
        with self._lock:
            dev = self.get_device(device_id, tenant_id)
            if dev:
                dev.trust_state = trust_state
                dev.updated_at = time.time()
                return dev
            return None

    def delete_device(self, device_id: str, tenant_id: str) -> bool:
        with self._lock:
            if tenant_id in self._devices and device_id in self._devices[tenant_id]:
                del self._devices[tenant_id][device_id]
                # Cascade delete capabilities
                if tenant_id in self._capabilities:
                    to_del = [cid for cid, cap in self._capabilities[tenant_id].items() if cap.device_id == device_id]
                    for cid in to_del:
                        del self._capabilities[tenant_id][cid]
                return True
            return False

    # 2. Capabilities
    def save_capability(self, capability: DeviceCapabilityRecord) -> DeviceCapabilityRecord:
        with self._lock:
            if capability.tenant_id not in self._capabilities:
                self._capabilities[capability.tenant_id] = {}
            capability.updated_at = time.time()
            self._capabilities[capability.tenant_id][capability.capability_id] = capability
            return capability

    def get_capability(self, capability_id: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        with self._lock:
            return self._capabilities.get(tenant_id, {}).get(capability_id)

    def get_capability_by_name(self, device_id: str, capability_name: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        with self._lock:
            for cap in self._capabilities.get(tenant_id, {}).values():
                if cap.device_id == device_id and cap.name == capability_name:
                    return cap
            return None

    def list_capabilities(self, device_id: str, tenant_id: str) -> list[DeviceCapabilityRecord]:
        with self._lock:
            return [
                c for c in self._capabilities.get(tenant_id, {}).values() if c.device_id == device_id
            ]

    def update_capability_auth(self, capability_id: str, tenant_id: str, auth_status: CapabilityAuthStatus) -> DeviceCapabilityRecord | None:
        with self._lock:
            cap = self.get_capability(capability_id, tenant_id)
            if cap:
                cap.auth_status = auth_status
                cap.updated_at = time.time()
                return cap
            return None

    # 3. Sessions
    def save_session(self, session: DeviceSessionRecord) -> DeviceSessionRecord:
        with self._lock:
            if session.tenant_id not in self._sessions:
                self._sessions[session.tenant_id] = {}
            self._sessions[session.tenant_id][session.session_id] = session
            return session

    def get_session(self, session_id: str, tenant_id: str) -> DeviceSessionRecord | None:
        with self._lock:
            return self._sessions.get(tenant_id, {}).get(session_id)

    # 4. Executions
    def save_execution(self, execution: DeviceExecutionRecord) -> DeviceExecutionRecord:
        with self._lock:
            if execution.tenant_id not in self._executions:
                self._executions[execution.tenant_id] = {}
            self._executions[execution.tenant_id][execution.execution_id] = execution
            return execution

    def get_execution(self, execution_id: str, tenant_id: str) -> DeviceExecutionRecord | None:
        with self._lock:
            return self._executions.get(tenant_id, {}).get(execution_id)

    def get_execution_by_idempotency(self, tenant_id: str, idempotency_key: str) -> DeviceExecutionRecord | None:
        with self._lock:
            for ex in self._executions.get(tenant_id, {}).values():
                if ex.idempotency_key == idempotency_key:
                    return ex
            return None

    def list_executions(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceExecutionRecord]:
        with self._lock:
            execs = [
                e for e in self._executions.get(tenant_id, {}).values() if e.device_id == device_id
            ]
            return sorted(execs, key=lambda e: e.created_at, reverse=True)[:limit]

    # 5. Audit Events
    def save_audit_event(self, event: DeviceAuditEvent) -> DeviceAuditEvent:
        with self._lock:
            if event.tenant_id not in self._audit_events:
                self._audit_events[event.tenant_id] = []
            self._audit_events[event.tenant_id].append(event)
            return event

    def list_audit_events(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceAuditEvent]:
        with self._lock:
            events = [
                e for e in self._audit_events.get(tenant_id, []) if e.device_id == device_id
            ]
            return sorted(events, key=lambda e: e.created_at, reverse=True)[:limit]

    # 6. Tenant Purge
    def purge_tenant_data(self, tenant_id: str) -> int:
        with self._lock:
            count = len(self._devices.get(tenant_id, {}))
            self._devices.pop(tenant_id, None)
            self._capabilities.pop(tenant_id, None)
            self._sessions.pop(tenant_id, None)
            self._executions.pop(tenant_id, None)
            self._audit_events.pop(tenant_id, None)
            return count
