"""M58 — Platform Adapters & Execution Unit Tests."""

import pytest
from core.platform.adapters.desktop import DesktopOSAdapter
from core.platform.adapters.simulated import SimulatedPlatformAdapter
from core.platform.gateway import PlatformIntegrationGateway
from core.platform.security import PlatformSecurityManager
from core.platform.types import ExecutionMode, ExecutionStatus
from core.repositories.in_memory_platform import InMemoryPlatformRepository


class TestAdaptersAndExecutionUnit:
    @pytest.fixture
    def desktop_adapter(self, tmp_path):
        sec = PlatformSecurityManager(sandbox_root=str(tmp_path))
        return DesktopOSAdapter(security_manager=sec)

    @pytest.fixture
    def simulated_adapter(self):
        return SimulatedPlatformAdapter()

    def test_desktop_adapter_real_system_queries(self, desktop_adapter):
        # Invariant M58-F11 & M58-F21
        assert desktop_adapter.execution_mode == ExecutionMode.REAL
        assert desktop_adapter.is_available() is True

        sys_info = desktop_adapter.execute("tenant_1", "get_system_info", {})
        assert "os_name" in sys_info
        assert "architecture" in sys_info
        assert "platform_type" in sys_info

        clock_info = desktop_adapter.execute("tenant_1", "get_clock", {})
        assert "utc_timestamp" in clock_info
        assert "local_time" in clock_info

        net_info = desktop_adapter.execute("tenant_1", "get_network_status", {})
        assert "hostname" in net_info
        assert net_info["connected"] is True

    def test_desktop_sandboxed_file_crud(self, desktop_adapter):
        # Invariant M58-F13
        # 1. Write file
        w_res = desktop_adapter.execute(
            "tenant_1",
            "write_sandboxed_file",
            {"path": "config.txt", "content": "port=8080\nenv=test"},
        )
        assert w_res["status"] == "written"
        assert w_res["bytes_written"] > 0

        # 2. Read file
        r_res = desktop_adapter.execute(
            "tenant_1",
            "read_sandboxed_file",
            {"path": "config.txt"},
        )
        assert "port=8080" in r_res["content"]

        # 3. List directory
        l_res = desktop_adapter.execute(
            "tenant_1",
            "list_sandboxed_directory",
            {},
        )
        assert l_res["count"] >= 1
        assert any(e["name"] == "config.txt" for e in l_res["entries"])

        # 4. Delete file
        d_res = desktop_adapter.execute(
            "tenant_1",
            "delete_sandboxed_file",
            {"path": "config.txt"},
        )
        assert d_res["status"] == "deleted"

    def test_simulated_adapter_execution(self, simulated_adapter):
        # Invariant M58-F21
        assert simulated_adapter.execution_mode == ExecutionMode.SIMULATED
        res = simulated_adapter.execute("tenant_1", "get_system_info", {})
        assert res["simulated"] is True
        assert res["os_name"] == "SimulatedOS"

    def test_gateway_idempotent_execution(self, tmp_path):
        # Invariant M58-F18 & TEST-M58-SEC-10
        sec = PlatformSecurityManager(sandbox_root=str(tmp_path))
        repo = InMemoryPlatformRepository()
        gateway = PlatformIntegrationGateway(repository=repo, security_manager=sec)

        dev = gateway.register_device(tenant_id="tenant_1", name="Dev Node", auto_authorize=True)

        # 1. First execution with idempotency key
        res1 = gateway.execute_action(
            tenant_id="tenant_1",
            device_id=dev.device_id,
            capability_name="get_clock",
            idempotency_key="idemp_repeat_123",
        )
        assert res1.status == ExecutionStatus.SUCCEEDED

        # 2. Replayed request returns cached result
        res2 = gateway.execute_action(
            tenant_id="tenant_1",
            device_id=dev.device_id,
            capability_name="get_clock",
            idempotency_key="idemp_repeat_123",
        )
        assert res2.execution_id == res1.execution_id
        assert res2.result == res1.result
