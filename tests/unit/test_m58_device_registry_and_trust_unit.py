"""M58 — Device Registry & Trust State Machine Unit Tests."""

import pytest
from core.platform.gateway import PlatformIntegrationGateway
from core.platform.registry import PlatformCapabilityRegistry
from core.platform.types import (
    CapabilityAuthStatus,
    CapabilityRiskLevel,
    DeviceTrustState,
    DeviceType,
    PlatformType,
)
from core.repositories.in_memory_platform import InMemoryPlatformRepository


class TestDeviceRegistryAndTrustUnit:
    @pytest.fixture
    def gateway(self):
        repo = InMemoryPlatformRepository()
        return PlatformIntegrationGateway(repository=repo)

    def test_device_registration_lifecycle(self, gateway):
        # Invariant M58-F01 & M58-F03
        dev = gateway.register_device(
            tenant_id="tenant_1",
            name="Developer Machine",
            device_type=DeviceType.DESKTOP,
            platform=PlatformType.WINDOWS,
            auto_authorize=False,
        )
        assert dev.trust_state == DeviceTrustState.PENDING_VERIFICATION
        assert dev.tenant_id == "tenant_1"

        # Declared capabilities are created in DECLARED status
        caps = gateway.repository.list_capabilities(dev.device_id, tenant_id="tenant_1")
        assert len(caps) > 0
        for c in caps:
            assert c.auth_status == CapabilityAuthStatus.DECLARED

    def test_unverified_device_execution_rejected(self, gateway):
        # Invariant M58-F05 & TEST-M58-SEC-02
        dev = gateway.register_device(tenant_id="tenant_1", name="Unverified Node", auto_authorize=False)
        with pytest.raises(PermissionError, match="has not been verified or authorized"):
            gateway.execute_action(tenant_id="tenant_1", device_id=dev.device_id, capability_name="get_clock")

    def test_trust_state_transitions_and_revocation(self, gateway):
        # Invariant M58-F05, M58-F06
        dev = gateway.register_device(tenant_id="tenant_1", name="Managed Node", auto_authorize=False)
        
        # 1. Authorize device
        authorized_dev = gateway.update_device_trust(
            tenant_id="tenant_1", device_id=dev.device_id, trust_state=DeviceTrustState.AUTHORIZED
        )
        assert authorized_dev.trust_state == DeviceTrustState.AUTHORIZED

        # 2. Suspend device
        suspended_dev = gateway.update_device_trust(
            tenant_id="tenant_1", device_id=dev.device_id, trust_state=DeviceTrustState.SUSPENDED
        )
        assert suspended_dev.trust_state == DeviceTrustState.SUSPENDED
        with pytest.raises(PermissionError, match="is SUSPENDED"):
            gateway.execute_action(tenant_id="tenant_1", device_id=dev.device_id, capability_name="get_clock")

        # 3. Revoke device
        revoked_dev = gateway.update_device_trust(
            tenant_id="tenant_1", device_id=dev.device_id, trust_state=DeviceTrustState.REVOKED
        )
        assert revoked_dev.trust_state == DeviceTrustState.REVOKED
        with pytest.raises(PermissionError, match="is REVOKED"):
            gateway.execute_action(tenant_id="tenant_1", device_id=dev.device_id, capability_name="get_clock")

    def test_capability_authorization_enforcement(self, gateway):
        # Invariant M58-F04 & TEST-M58-SEC-03
        dev = gateway.register_device(tenant_id="tenant_1", name="Authorized Node", auto_authorize=True)
        # Explicitly revoke single capability
        gateway.authorize_capability(
            tenant_id="tenant_1",
            device_id=dev.device_id,
            capability_name="get_battery_status",
            auth_status=CapabilityAuthStatus.DISABLED,
        )

        with pytest.raises(PermissionError, match="is not authorized"):
            gateway.execute_action(
                tenant_id="tenant_1", device_id=dev.device_id, capability_name="get_battery_status"
            )
