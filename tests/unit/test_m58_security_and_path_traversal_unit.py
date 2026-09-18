"""M58 — Security, Path Traversal, Command Injection & SSRF Unit Tests."""

import pytest
from core.platform.gateway import PlatformIntegrationGateway
from core.platform.security import PlatformSecurityManager
from core.platform.types import CapabilityAuthStatus, CapabilityRiskLevel
from core.repositories.in_memory_platform import InMemoryPlatformRepository


class TestSecurityAndPathTraversalUnit:
    @pytest.fixture
    def security_mgr(self, tmp_path):
        return PlatformSecurityManager(sandbox_root=str(tmp_path))

    def test_path_traversal_parent_directory_rejected(self, security_mgr):
        # Invariant M58-F13 & TEST-M58-SEC-06
        with pytest.raises(PermissionError, match="Path traversal detected"):
            security_mgr.sanitize_path("tenant_1", "../secret.txt")

        with pytest.raises(PermissionError, match="Path traversal detected"):
            security_mgr.sanitize_path("tenant_1", "../../windows/system32/cmd.exe")

        with pytest.raises(PermissionError, match="Path traversal detected"):
            security_mgr.sanitize_path("tenant_1", "/etc/shadow")

        with pytest.raises(PermissionError, match="Path traversal detected"):
            security_mgr.sanitize_path("tenant_1", "C:\\Windows\\System32")

    def test_command_safety_blocks_unauthorized_shell(self, security_mgr):
        # Invariant M58-F11, M58-F12 & TEST-M58-SEC-07, TEST-M58-SEC-08
        with pytest.raises(PermissionError, match="is not in allowlisted"):
            security_mgr.validate_command_safety("execute_raw_powershell")

        with pytest.raises(PermissionError, match="Potential command injection"):
            security_mgr.validate_command_safety("read_sandboxed_file", ["file.txt; rm -rf /"])

        with pytest.raises(PermissionError, match="Potential command injection"):
            security_mgr.validate_command_safety("read_sandboxed_file", ["file.txt | powershell"])

    def test_ssrf_forbidden_metadata_hosts(self, security_mgr):
        # Invariant M58-F14 & TEST-M58-SEC-09
        with pytest.raises(PermissionError, match="SSRF violation"):
            security_mgr.validate_endpoint_url("http://169.254.169.254/latest/meta-data")

        with pytest.raises(PermissionError, match="SSRF violation"):
            security_mgr.validate_endpoint_url("http://metadata.google.internal/computeMetadata/v1/")

        with pytest.raises(PermissionError, match="SSRF violation"):
            security_mgr.validate_endpoint_url("http://127.0.0.1:8080/admin")

    def test_human_approval_gating_for_high_risk_actions(self, tmp_path):
        # Invariant M58-F08 & TEST-M58-SEC-04
        sec = PlatformSecurityManager(sandbox_root=str(tmp_path))
        repo = InMemoryPlatformRepository()
        gateway = PlatformIntegrationGateway(repository=repo, security_manager=sec)

        dev = gateway.register_device(tenant_id="tenant_1", name="Dev Node", auto_authorize=True)

        # 1. Attempting high-risk action (delete_sandboxed_file) without approval token fails closed
        with pytest.raises(PermissionError, match="requires a valid M48 human approval token"):
            gateway.execute_action(
                tenant_id="tenant_1",
                device_id=dev.device_id,
                capability_name="delete_sandboxed_file",
                parameters={"path": "file.txt"},
                approval_token=None,
            )

        # 2. Supplying valid approval token allows execution
        # Write file first so deletion succeeds
        gateway.execute_action(
            tenant_id="tenant_1",
            device_id=dev.device_id,
            capability_name="write_sandboxed_file",
            parameters={"path": "file.txt", "content": "delete_me"},
        )

        res = gateway.execute_action(
            tenant_id="tenant_1",
            device_id=dev.device_id,
            capability_name="delete_sandboxed_file",
            parameters={"path": "file.txt"},
            approval_token="appr_token_valid_123",
        )
        assert res.status.value == "succeeded"
