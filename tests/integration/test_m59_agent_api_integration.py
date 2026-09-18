"""M59 — Unified Autonomous Agent REST API Integration Tests."""

import json
import pytest
from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import TokenAuthenticator
from core.identity import UserIdentity, UserRole
from core.repositories.factory import create_in_memory_repositories


class TestAgentApiIntegration:
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
            user_id="user_alice",
            username="alice",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        user_b = UserIdentity(
            user_id="user_bob",
            username="bob",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        repos.users.save(user_a)
        repos.users.save(user_b)

        token_a = "token_alice_agent_secret_12345"
        token_b = "token_bob_agent_secret_12345"
        repos.tokens.register_token(token_a, "user_alice")
        repos.tokens.register_token(token_b, "user_bob")

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

                if method in ("POST", "PUT", "PATCH"):
                    effective_data = data if data is not None else {}
                    body_bytes = json.dumps(effective_data).encode("utf-8")
                else:
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
        # Invariant M59-F02
        status, _ = test_client.request("GET", "/v1/agent/runs", no_token=True)
        assert status == 401

    def test_list_mesh_roles(self, test_client):
        # Invariant M59-F27
        status, resp = test_client.request("GET", "/v1/agent/mesh/roles")
        assert status == 200
        assert "roles" in resp
        roles = [r["role"] for r in resp["roles"]]
        assert "research" in roles
        assert "generalist" in roles

    def test_create_and_execute_agent_run(self, test_client):
        # Invariants M59-F01, M59-F03
        payload = {
            "intent": "Calculate 15 + 25 * 3",
            "auto_execute": True,
        }
        status, resp = test_client.request("POST", "/v1/agent/runs", data=payload)
        assert status == 201
        run_id = resp["run_id"]
        assert run_id.startswith("run_")
        assert resp["tenant_id"] == "user_alice"
        assert resp["status"] in ("completed", "running")

        # Get run details
        g_status, g_resp = test_client.request("GET", f"/v1/agent/runs/{run_id}")
        assert g_status == 200
        assert g_resp["run_id"] == run_id
        assert "steps" in g_resp
        assert len(g_resp["steps"]) > 0

        # Get run events
        e_status, e_resp = test_client.request("GET", f"/v1/agent/runs/{run_id}/events")
        assert e_status == 200
        assert "events" in e_resp
        assert len(e_resp["events"]) > 0

    def test_pause_resume_cancel_agent_run(self, test_client):
        # Invariants M59-F04, M59-F06
        # Create without auto-executing
        payload = {
            "intent": "Long running batch analysis",
            "auto_execute": False,
        }
        status, resp = test_client.request("POST", "/v1/agent/runs", data=payload)
        assert status == 201
        run_id = resp["run_id"]
        assert resp["status"] == "pending"

        # Pause run
        p_status, p_resp = test_client.request("POST", f"/v1/agent/runs/{run_id}/pause")
        assert p_status == 200
        assert p_resp["status"] == "paused"

        # Cancel run
        c_status, c_resp = test_client.request("POST", f"/v1/agent/runs/{run_id}/cancel")
        assert c_status == 200
        assert c_resp["status"] == "cancelled"

    def test_cross_tenant_isolation_and_purge(self, test_client):
        # Invariants M59-F17, M59-F50
        # Alice creates a run
        status, resp = test_client.request("POST", "/v1/agent/runs", data={"intent": "Alice private calculation", "auto_execute": True})
        assert status == 201
        alice_run_id = resp["run_id"]

        # Bob attempts to get Alice's run -> 404 Not Found (isolated)
        bob_status, _ = test_client.request("GET", f"/v1/agent/runs/{alice_run_id}", use_other_token=True)
        assert bob_status == 404

        # Bob attempts to cancel Alice's run -> 404 Not Found
        bob_cancel_st, _ = test_client.request("POST", f"/v1/agent/runs/{alice_run_id}/cancel", use_other_token=True)
        assert bob_cancel_st == 404

        # Bob attempts to purge Alice's data -> 403 Forbidden
        bob_purge_st, _ = test_client.request("DELETE", "/v1/agent/tenants/user_alice/purge", use_other_token=True)
        assert bob_purge_st == 403

        # Alice purges own data -> 200 OK
        alice_purge_st, purge_resp = test_client.request("DELETE", "/v1/agent/tenants/user_alice/purge")
        assert alice_purge_st == 200
        assert purge_resp["success"] is True
