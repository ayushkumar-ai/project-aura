"""M58 — Platform and Device Types Unit Tests."""

import time
import pytest
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


class TestPlatformTypesUnit:
    def test_device_record_defaults_and_serialization(self):
        # Invariant M58-F01 & M58-F03
        dev = DeviceRecord(
            tenant_id="tenant_alpha",
            name="Primary Workstation",
            device_type=DeviceType.DESKTOP,
            platform=PlatformType.WINDOWS,
            metadata={"secret_key": "Bearer sk-12345abcdefghijklmnopqrstuvwxyz", "env": "prod"},
        )
        assert dev.device_id.startswith("dev_")
        assert dev.trust_state == DeviceTrustState.PENDING_VERIFICATION
        # Verify secret scrubbing in metadata (Invariant M58-F29)
        assert "sk-12345" not in dev.metadata.get("secret_key", "")

        serialized = dev.to_dict()
        assert serialized["device_id"] == dev.device_id
        assert serialized["tenant_id"] == "tenant_alpha"
        assert serialized["platform"] == "windows"

        reconstructed = DeviceRecord.from_dict(serialized)
        assert reconstructed.device_id == dev.device_id
        assert reconstructed.platform == PlatformType.WINDOWS

    def test_capability_risk_and_approval_flag(self):
        # Invariant M58-F08
        low_cap = DeviceCapabilityRecord(
            device_id="dev_1",
            tenant_id="tenant_alpha",
            name="get_clock",
            risk_level=CapabilityRiskLevel.LOW,
        )
        assert low_cap.requires_approval is False

        high_cap = DeviceCapabilityRecord(
            device_id="dev_1",
            tenant_id="tenant_alpha",
            name="delete_sandboxed_file",
            risk_level=CapabilityRiskLevel.HIGH,
        )
        assert high_cap.requires_approval is True

        crit_cap = DeviceCapabilityRecord(
            device_id="dev_1",
            tenant_id="tenant_alpha",
            name="security_setting_change",
            risk_level=CapabilityRiskLevel.CRITICAL,
        )
        assert crit_cap.requires_approval is True

    def test_execution_record_roundtrip(self):
        # Invariant M58-F18
        rec = DeviceExecutionRecord(
            tenant_id="tenant_beta",
            device_id="dev_2",
            capability_name="get_system_info",
            risk_level=CapabilityRiskLevel.LOW,
            status=ExecutionStatus.SUCCEEDED,
            execution_mode=ExecutionMode.REAL,
            idempotency_key="idemp_key_123",
            result={"os": "Windows"},
            duration_ms=12.5,
        )
        d = rec.to_dict()
        assert d["execution_id"] == rec.execution_id
        assert d["status"] == "succeeded"
        assert d["execution_mode"] == "real"

        reconstructed = DeviceExecutionRecord.from_dict(d)
        assert reconstructed.execution_id == rec.execution_id
        assert reconstructed.idempotency_key == "idemp_key_123"
        assert reconstructed.duration_ms == 12.5

    def test_platform_limits_config(self):
        limits = PlatformLimitsConfig(max_execution_timeout_seconds=45.0, max_retries=5)
        assert limits.max_execution_timeout_seconds == 45.0
        assert limits.max_retries == 5
        assert limits.require_approval_for_high_risk is True
