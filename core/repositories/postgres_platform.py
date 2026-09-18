"""M58 — PostgreSQL Platform & Device Repository Implementation.

Provides production-grade PostgreSQL 16 persistence for migration 009.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.database import DatabaseConnectionPool
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
    PlatformType,
)
from core.repositories.base_platform import BasePlatformRepository

logger = logging.getLogger("aura.repositories.postgres_platform")


def _to_timestamp(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc).timestamp()
    return 0.0


def _json_dumps(val: Any) -> str:
    try:
        return json.dumps(val)
    except Exception:
        return "{}"


def _json_loads(val: Any) -> Any:
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


class PostgresPlatformRepository(BasePlatformRepository):
    """PostgreSQL 16 persistence for devices, capabilities, sessions, executions, and audits."""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool

    # 1. Devices
    def save_device(self, device: DeviceRecord) -> DeviceRecord:
        sql = """
        INSERT INTO devices (
            device_id, tenant_id, name, device_type, platform,
            platform_version, trust_state, hostname, metadata,
            registered_at, last_seen_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), TO_TIMESTAMP(%s)
        )
        ON CONFLICT (device_id) DO UPDATE SET
            name = EXCLUDED.name,
            device_type = EXCLUDED.device_type,
            platform = EXCLUDED.platform,
            platform_version = EXCLUDED.platform_version,
            trust_state = EXCLUDED.trust_state,
            hostname = EXCLUDED.hostname,
            metadata = EXCLUDED.metadata,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = EXCLUDED.updated_at
        RETURNING device_id, tenant_id, name, device_type, platform,
                  platform_version, trust_state, hostname, metadata,
                  EXTRACT(EPOCH FROM registered_at),
                  EXTRACT(EPOCH FROM last_seen_at),
                  EXTRACT(EPOCH FROM updated_at);
        """
        params = (
            device.device_id,
            device.tenant_id,
            device.name,
            device.device_type.value if isinstance(device.device_type, DeviceType) else str(device.device_type),
            device.platform.value if isinstance(device.platform, PlatformType) else str(device.platform),
            device.platform_version,
            device.trust_state.value if isinstance(device.trust_state, DeviceTrustState) else str(device.trust_state),
            device.hostname,
            _json_dumps(device.metadata),
            device.registered_at,
            device.last_seen_at,
            device.updated_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_device(row)
        return device

    def _row_to_device(self, row: tuple) -> DeviceRecord:
        return DeviceRecord(
            device_id=row[0],
            tenant_id=row[1],
            name=row[2],
            device_type=DeviceType(row[3]),
            platform=PlatformType(row[4]),
            platform_version=row[5],
            trust_state=DeviceTrustState(row[6]),
            hostname=row[7],
            metadata=_json_loads(row[8]),
            registered_at=_to_timestamp(row[9]),
            last_seen_at=_to_timestamp(row[10]),
            updated_at=_to_timestamp(row[11]),
        )

    def get_device(self, device_id: str, tenant_id: str) -> DeviceRecord | None:
        sql = """
        SELECT device_id, tenant_id, name, device_type, platform,
               platform_version, trust_state, hostname, metadata,
               EXTRACT(EPOCH FROM registered_at),
               EXTRACT(EPOCH FROM last_seen_at),
               EXTRACT(EPOCH FROM updated_at)
        FROM devices
        WHERE device_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_device(row)
        return None

    def list_devices(self, tenant_id: str) -> list[DeviceRecord]:
        sql = """
        SELECT device_id, tenant_id, name, device_type, platform,
               platform_version, trust_state, hostname, metadata,
               EXTRACT(EPOCH FROM registered_at),
               EXTRACT(EPOCH FROM last_seen_at),
               EXTRACT(EPOCH FROM updated_at)
        FROM devices
        WHERE tenant_id = %s
        ORDER BY registered_at ASC;
        """
        devices = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id,))
                for row in cur.fetchall():
                    devices.append(self._row_to_device(row))
        return devices

    def update_device_trust(self, device_id: str, tenant_id: str, trust_state: DeviceTrustState) -> DeviceRecord | None:
        sql = """
        UPDATE devices
        SET trust_state = %s, updated_at = CURRENT_TIMESTAMP
        WHERE device_id = %s AND tenant_id = %s
        RETURNING device_id, tenant_id, name, device_type, platform,
                  platform_version, trust_state, hostname, metadata,
                  EXTRACT(EPOCH FROM registered_at),
                  EXTRACT(EPOCH FROM last_seen_at),
                  EXTRACT(EPOCH FROM updated_at);
        """
        state_str = trust_state.value if isinstance(trust_state, DeviceTrustState) else str(trust_state)
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (state_str, device_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_device(row)
        return None

    def delete_device(self, device_id: str, tenant_id: str) -> bool:
        sql = "DELETE FROM devices WHERE device_id = %s AND tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, tenant_id))
                return cur.rowcount > 0

    # 2. Capabilities
    def save_capability(self, capability: DeviceCapabilityRecord) -> DeviceCapabilityRecord:
        sql = """
        INSERT INTO device_capabilities (
            capability_id, device_id, tenant_id, name, risk_level,
            auth_status, description, parameters_schema, requires_approval, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, TO_TIMESTAMP(%s)
        )
        ON CONFLICT (capability_id) DO UPDATE SET
            name = EXCLUDED.name,
            risk_level = EXCLUDED.risk_level,
            auth_status = EXCLUDED.auth_status,
            description = EXCLUDED.description,
            parameters_schema = EXCLUDED.parameters_schema,
            requires_approval = EXCLUDED.requires_approval,
            updated_at = EXCLUDED.updated_at
        RETURNING capability_id, device_id, tenant_id, name, risk_level,
                  auth_status, description, parameters_schema, requires_approval,
                  EXTRACT(EPOCH FROM updated_at);
        """
        params = (
            capability.capability_id,
            capability.device_id,
            capability.tenant_id,
            capability.name,
            capability.risk_level.value if isinstance(capability.risk_level, CapabilityRiskLevel) else str(capability.risk_level),
            capability.auth_status.value if isinstance(capability.auth_status, CapabilityAuthStatus) else str(capability.auth_status),
            capability.description,
            _json_dumps(capability.parameters_schema),
            capability.requires_approval,
            capability.updated_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_capability(row)
        return capability

    def _row_to_capability(self, row: tuple) -> DeviceCapabilityRecord:
        return DeviceCapabilityRecord(
            capability_id=row[0],
            device_id=row[1],
            tenant_id=row[2],
            name=row[3],
            risk_level=CapabilityRiskLevel(row[4]),
            auth_status=CapabilityAuthStatus(row[5]),
            description=row[6],
            parameters_schema=_json_loads(row[7]),
            requires_approval=bool(row[8]),
            updated_at=_to_timestamp(row[9]),
        )

    def get_capability(self, capability_id: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        sql = """
        SELECT capability_id, device_id, tenant_id, name, risk_level,
               auth_status, description, parameters_schema, requires_approval,
               EXTRACT(EPOCH FROM updated_at)
        FROM device_capabilities
        WHERE capability_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (capability_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_capability(row)
        return None

    def get_capability_by_name(self, device_id: str, capability_name: str, tenant_id: str) -> DeviceCapabilityRecord | None:
        sql = """
        SELECT capability_id, device_id, tenant_id, name, risk_level,
               auth_status, description, parameters_schema, requires_approval,
               EXTRACT(EPOCH FROM updated_at)
        FROM device_capabilities
        WHERE device_id = %s AND name = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, capability_name, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_capability(row)
        return None

    def list_capabilities(self, device_id: str, tenant_id: str) -> list[DeviceCapabilityRecord]:
        sql = """
        SELECT capability_id, device_id, tenant_id, name, risk_level,
               auth_status, description, parameters_schema, requires_approval,
               EXTRACT(EPOCH FROM updated_at)
        FROM device_capabilities
        WHERE device_id = %s AND tenant_id = %s
        ORDER BY name ASC;
        """
        caps = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, tenant_id))
                for row in cur.fetchall():
                    caps.append(self._row_to_capability(row))
        return caps

    def update_capability_auth(self, capability_id: str, tenant_id: str, auth_status: CapabilityAuthStatus) -> DeviceCapabilityRecord | None:
        sql = """
        UPDATE device_capabilities
        SET auth_status = %s, updated_at = CURRENT_TIMESTAMP
        WHERE capability_id = %s AND tenant_id = %s
        RETURNING capability_id, device_id, tenant_id, name, risk_level,
                  auth_status, description, parameters_schema, requires_approval,
                  EXTRACT(EPOCH FROM updated_at);
        """
        status_str = auth_status.value if isinstance(auth_status, CapabilityAuthStatus) else str(auth_status)
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status_str, capability_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_capability(row)
        return None

    # 3. Sessions
    def save_session(self, session: DeviceSessionRecord) -> DeviceSessionRecord:
        sql = """
        INSERT INTO device_sessions (
            session_id, device_id, tenant_id, status, ip_address,
            started_at, expires_at, last_heartbeat_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            TO_TIMESTAMP(%s), %s, TO_TIMESTAMP(%s)
        )
        ON CONFLICT (session_id) DO UPDATE SET
            status = EXCLUDED.status,
            last_heartbeat_at = EXCLUDED.last_heartbeat_at;
        """
        exp = f"TO_TIMESTAMP({session.expires_at})" if session.expires_at else None
        params = (
            session.session_id,
            session.device_id,
            session.tenant_id,
            session.status,
            session.ip_address,
            session.started_at,
            exp,
            session.last_heartbeat_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return session

    def get_session(self, session_id: str, tenant_id: str) -> DeviceSessionRecord | None:
        sql = """
        SELECT session_id, device_id, tenant_id, status, ip_address,
               EXTRACT(EPOCH FROM started_at),
               EXTRACT(EPOCH FROM expires_at),
               EXTRACT(EPOCH FROM last_heartbeat_at)
        FROM device_sessions
        WHERE session_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (session_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return DeviceSessionRecord(
                        session_id=row[0],
                        device_id=row[1],
                        tenant_id=row[2],
                        status=row[3],
                        ip_address=row[4],
                        started_at=_to_timestamp(row[5]),
                        expires_at=_to_timestamp(row[6]) if row[6] is not None else None,
                        last_heartbeat_at=_to_timestamp(row[7]),
                    )
        return None

    # 4. Executions
    def save_execution(self, execution: DeviceExecutionRecord) -> DeviceExecutionRecord:
        sql = """
        INSERT INTO device_execution_records (
            execution_id, tenant_id, device_id, capability_name, risk_level,
            status, execution_mode, idempotency_key, approval_token,
            parameters, result, error_detail, duration_ms, created_at, completed_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, TO_TIMESTAMP(%s), %s
        )
        ON CONFLICT (execution_id) DO UPDATE SET
            status = EXCLUDED.status,
            result = EXCLUDED.result,
            error_detail = EXCLUDED.error_detail,
            duration_ms = EXCLUDED.duration_ms,
            completed_at = EXCLUDED.completed_at
        RETURNING execution_id, tenant_id, device_id, capability_name, risk_level,
                  status, execution_mode, idempotency_key, approval_token,
                  parameters, result, error_detail, duration_ms,
                  EXTRACT(EPOCH FROM created_at),
                  EXTRACT(EPOCH FROM completed_at);
        """
        comp = f"TO_TIMESTAMP({execution.completed_at})" if execution.completed_at else None
        params = (
            execution.execution_id,
            execution.tenant_id,
            execution.device_id,
            execution.capability_name,
            execution.risk_level.value if isinstance(execution.risk_level, CapabilityRiskLevel) else str(execution.risk_level),
            execution.status.value if isinstance(execution.status, ExecutionStatus) else str(execution.status),
            execution.execution_mode.value if isinstance(execution.execution_mode, ExecutionMode) else str(execution.execution_mode),
            execution.idempotency_key,
            execution.approval_token,
            _json_dumps(execution.parameters),
            _json_dumps(execution.result),
            execution.error_detail,
            execution.duration_ms,
            execution.created_at,
            comp,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_execution(row)
        return execution

    def _row_to_execution(self, row: tuple) -> DeviceExecutionRecord:
        return DeviceExecutionRecord(
            execution_id=row[0],
            tenant_id=row[1],
            device_id=row[2],
            capability_name=row[3],
            risk_level=CapabilityRiskLevel(row[4]),
            status=ExecutionStatus(row[5]),
            execution_mode=ExecutionMode(row[6]),
            idempotency_key=row[7],
            approval_token=row[8],
            parameters=_json_loads(row[9]),
            result=_json_loads(row[10]),
            error_detail=row[11],
            duration_ms=float(row[12]),
            created_at=_to_timestamp(row[13]),
            completed_at=_to_timestamp(row[14]) if row[14] is not None else None,
        )

    def get_execution(self, execution_id: str, tenant_id: str) -> DeviceExecutionRecord | None:
        sql = """
        SELECT execution_id, tenant_id, device_id, capability_name, risk_level,
               status, execution_mode, idempotency_key, approval_token,
               parameters, result, error_detail, duration_ms,
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM completed_at)
        FROM device_execution_records
        WHERE execution_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (execution_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_execution(row)
        return None

    def get_execution_by_idempotency(self, tenant_id: str, idempotency_key: str) -> DeviceExecutionRecord | None:
        sql = """
        SELECT execution_id, tenant_id, device_id, capability_name, risk_level,
               status, execution_mode, idempotency_key, approval_token,
               parameters, result, error_detail, duration_ms,
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM completed_at)
        FROM device_execution_records
        WHERE tenant_id = %s AND idempotency_key = %s
        ORDER BY created_at DESC
        LIMIT 1;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, idempotency_key))
                row = cur.fetchone()
                if row:
                    return self._row_to_execution(row)
        return None

    def list_executions(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceExecutionRecord]:
        sql = """
        SELECT execution_id, tenant_id, device_id, capability_name, risk_level,
               status, execution_mode, idempotency_key, approval_token,
               parameters, result, error_detail, duration_ms,
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM completed_at)
        FROM device_execution_records
        WHERE device_id = %s AND tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s;
        """
        execs = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, tenant_id, limit))
                for row in cur.fetchall():
                    execs.append(self._row_to_execution(row))
        return execs

    # 5. Audit Events
    def save_audit_event(self, event: DeviceAuditEvent) -> DeviceAuditEvent:
        sql = """
        INSERT INTO device_audit_events (
            event_id, tenant_id, device_id, action, principal_id,
            event_type, risk_level, details, created_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, TO_TIMESTAMP(%s)
        );
        """
        params = (
            event.event_id,
            event.tenant_id,
            event.device_id,
            event.action,
            event.principal_id,
            event.event_type,
            event.risk_level.value if isinstance(event.risk_level, CapabilityRiskLevel) else str(event.risk_level),
            _json_dumps(event.details),
            event.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return event

    def list_audit_events(self, device_id: str, tenant_id: str, limit: int = 50) -> list[DeviceAuditEvent]:
        sql = """
        SELECT event_id, tenant_id, device_id, action, principal_id,
               event_type, risk_level, details,
               EXTRACT(EPOCH FROM created_at)
        FROM device_audit_events
        WHERE device_id = %s AND tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s;
        """
        events = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (device_id, tenant_id, limit))
                for row in cur.fetchall():
                    events.append(
                        DeviceAuditEvent(
                            event_id=row[0],
                            tenant_id=row[1],
                            device_id=row[2],
                            action=row[3],
                            principal_id=row[4],
                            event_type=row[5],
                            risk_level=CapabilityRiskLevel(row[6]),
                            details=_json_loads(row[7]),
                            created_at=_to_timestamp(row[8]),
                        )
                    )
        return events

    # 6. Tenant Purge
    def purge_tenant_data(self, tenant_id: str) -> int:
        sql = "DELETE FROM devices WHERE tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id,))
                return cur.rowcount
