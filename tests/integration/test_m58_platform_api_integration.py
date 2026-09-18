"""M58 — Platform & Device REST API Integration Tests."""

import json
import pytest
from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import TokenAuthenticator
from core.identity import UserIdentity, UserRole
from core.repositories.factory import create_in_memory_repositories


class TestPlatformApiIntegration:
    @pytest.fixture
    def test_client(self):
        import urllib.request
        from urllib.error import HTTPError

        cfg = Settings(
            aura_env="development",
            aura_api_key_auth_enabled=True,
            aura_task_worker_enabled=False,
            aura_automations_enabled=False,
            aura_webhooks_enabled=False,
        )
        repos = create_in_memory_repositories()

        # Create user identities & tokens
        user_a = UserIdentity(
            user_id="user_dev_alice",
            username="alice",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        user_b = UserIdentity(
            user_id="user_dev_bob",
            username="bob",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        repos.users.save(user_a)
        repos.users.save(user_b)

        token_a = "token_alice_device_secret_123"
        token_b = "token_bob_device_secret_123"
        repos.tokens.register_token(token_a, "user_dev_alice")
        repos.tokens.register_token(token_b, "user_dev_bob")

        auth = TokenAuthenticator(token_repo=repos.tokens)
        server = AURAHTTPServer(config=cfg, host="127.0.0.1", port=0, authenticator=auth, repository_container=repos)
        server.start(block=False)
        port = server._server.server_address[1]

        class ClientWrapper:
            def __init__(self, port, token_a, token_b):
                self.base_url = f"http://127.0.0.1:{port}"
                self.token_a = token_a
                self.token_b = token_b

            def request(self, method, path, data=None, use_other_token=False, no_token=False):
                url = f"{self.base_url}{path}"
                headers = {"Content-Type": "application/json"}
                if not no_token:
                    tok = self.token_b if use_other_token else self.token_a
                    headers["Authorization"] = f"Bearer {tok}"

                body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
                req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))
                except HTTPError as e:
                    body = e.read().decode("utf-8")
                    try:
                        return e.code, json.loads(body)
                    except Exception:
                        return e.code, {"error": body}

        client = ClientWrapper(port, token_a, token_b)
        yield client
        server.stop()

    def test_unauthorized_access_rejected(self, test_client):
        # Invariant M58-F02 & TEST-M58-SEC-11
        status, _ = test_client.request("GET", "/v1/devices", no_token=True)
        assert status == 401

    def test_list_platform_capabilities(self, test_client):
        status, resp = test_client.request("GET", "/v1/platform/capabilities")
        assert status == 200
        assert "capabilities" in resp
        cap_names = [c["name"] for c in resp["capabilities"]]
        assert "get_system_info" in cap_names
        assert "get_clock" in cap_names
        assert "delete_sandboxed_file" in cap_names

    def test_device_registration_execution_and_isolation(self, test_client):
        # 1. Register Device for Alice
        reg_payload = {
            "name": "Alice Office PC",
            "device_type": "desktop",
            "platform": "windows",
            "auto_authorize": True,
        }
        status, dev_resp = test_client.request("POST", "/v1/devices", data=reg_payload)
        assert status == 201
        device_id = dev_resp["device_id"]
        assert dev_resp["name"] == "Alice Office PC"
        assert dev_resp["trust_state"] == "authorized"

        # 2. Get Device Details
        g_status, g_resp = test_client.request("GET", f"/v1/devices/{device_id}")
        assert g_status == 200
        assert g_resp["device_id"] == device_id

        # 3. List Device Capabilities
        c_status, c_resp = test_client.request("GET", f"/v1/devices/{device_id}/capabilities")
        assert c_status == 200
        assert c_resp["count"] > 0

        # 4. Execute Action on Device
        exec_payload = {
            "capability_name": "get_clock",
            "parameters": {},
            "admit_to_memory": True,
        }
        e_status, e_resp = test_client.request("POST", f"/v1/devices/{device_id}/execute", data=exec_payload)
        assert e_status == 200
        assert e_resp["status"] == "succeeded"
        assert "data" in e_resp["result"]

        # 5. Cross-Tenant Isolation: Bob attempts to access Alice's device (TEST-M58-SEC-01)
        b_status, _ = test_client.request("GET", f"/v1/devices/{device_id}", use_other_token=True)
        assert b_status == 404

        # 6. Delete Device
        d_status, d_resp = test_client.request("DELETE", f"/v1/devices/{device_id}")
        assert d_status == 200
        assert d_resp["deleted"] is True
