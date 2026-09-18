"""M57 — Multimodal REST API Integration Tests."""

import base64
import json
import pytest
from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import TokenAuthenticator
from core.identity import UserIdentity, UserRole
from core.repositories.factory import create_in_memory_repositories


class TestMultimodalApiIntegration:
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

        # Create user identity & token
        user_a = UserIdentity(
            user_id="user_mm_1",
            username="alice",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        user_b = UserIdentity(
            user_id="user_mm_2",
            username="bob",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        repos.users.save(user_a)
        repos.users.save(user_b)

        raw_token = "token_alice_secret_12345"
        other_token = "token_bob_secret_12345"
        repos.tokens.register_token(raw_token, "user_mm_1")
        repos.tokens.register_token(other_token, "user_mm_2")

        auth = TokenAuthenticator(token_repo=repos.tokens)

        server = AURAHTTPServer(config=cfg, host="127.0.0.1", port=0, authenticator=auth, repository_container=repos)
        server.start(block=False)
        port = server._server.server_address[1]

        class ClientWrapper:
            def __init__(self, port, token, other_token):
                self.base_url = f"http://127.0.0.1:{port}"
                self.token = token
                self.other_token = other_token

            def request(self, method, path, data=None, use_other_token=False, no_token=False):
                url = f"{self.base_url}{path}"
                headers = {"Content-Type": "application/json"}
                if not no_token:
                    tok = self.other_token if use_other_token else self.token
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

        client = ClientWrapper(port, raw_token, other_token)
        yield client
        server.stop()

    def test_unauthorized_access_rejected(self, test_client):
        # Invariant M57-F03 & TEST-M57-SEC-05
        status, resp = test_client.request("GET", "/v1/multimodal/artifacts", no_token=True)
        assert status == 401

    def test_list_capabilities(self, test_client):
        status, resp = test_client.request("GET", "/v1/multimodal/capabilities")
        assert status == 200
        assert "capabilities" in resp
        cap_ids = [c["capability_id"] for c in resp["capabilities"]]
        assert "image_understanding" in cap_ids
        assert "audio_transcription" in cap_ids

    def test_ingest_and_process_artifact_api(self, test_client):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        b64_content = base64.b64encode(png_bytes).decode("utf-8")

        # 1. Upload Artifact
        upload_payload = {
            "content_base64": b64_content,
            "filename": "chart.png",
            "format": "image/png",
            "media_type": "image",
        }
        status, resp = test_client.request("POST", "/v1/multimodal/artifacts", data=upload_payload)
        assert status == 201
        artifact_id = resp["artifact_id"]
        assert resp["media_type"] == "image"
        assert resp["lifecycle_state"] == "accepted"

        # 2. Process Artifact
        process_payload = {
            "artifact_id": artifact_id,
            "operation": "understand",
            "user_prompt": "Describe chart",
            "admit_to_memory": True,
        }
        p_status, p_resp = test_client.request("POST", "/v1/multimodal/process", data=process_payload)
        assert p_status == 200
        assert "result" in p_resp
        assert p_resp["job"]["status"] == "completed"

        # 3. Query Results
        r_status, r_resp = test_client.request("GET", f"/v1/multimodal/results?artifact_id={artifact_id}")
        assert r_status == 200
        assert r_resp["count"] >= 1

        # 4. Cross-Tenant Isolation: Tenant B cannot access Tenant A's artifact (TEST-M57-SEC-05)
        b_status, _ = test_client.request("GET", f"/v1/multimodal/artifacts/{artifact_id}", use_other_token=True)
        assert b_status == 404

        # 5. Delete Artifact (Cascading)
        del_status, del_resp = test_client.request("DELETE", f"/v1/multimodal/artifacts/{artifact_id}")
        assert del_status == 200
        assert del_resp["deleted"] is True
