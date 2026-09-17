"""M56 — Cognitive Memory REST API Integration Tests.

Tests HTTP endpoints for cognitive memory CRUD, contradiction tracking & resolution,
profile management, continuous learning feedback loops, context assembly, and GDPR purging.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import TokenAuthenticator
from core.identity import UserIdentity, UserRole
from core.repositories.factory import create_in_memory_repositories


@pytest.fixture
def test_server():
    repos = create_in_memory_repositories()

    user_a = UserIdentity(
        user_id="user_alpha",
        username="user_alpha",
        roles=frozenset({UserRole.USER}),
        is_authenticated=True,
    )
    user_b = UserIdentity(
        user_id="user_beta",
        username="user_beta",
        roles=frozenset({UserRole.USER}),
        is_authenticated=True,
    )
    repos.users.save(user_a)
    repos.users.save(user_b)

    token_a = "token_alpha_secret_12345"
    token_b = "token_beta_secret_12345"
    repos.tokens.register_token(token_a, "user_alpha")
    repos.tokens.register_token(token_b, "user_beta")

    authenticator = TokenAuthenticator(token_repo=repos.tokens)

    cfg = Settings(
        aura_env="development",
        aura_api_key_auth_enabled=True,
        aura_task_worker_enabled=False,
        aura_automations_enabled=False,
        aura_webhooks_enabled=False,
    )

    server = AURAHTTPServer(
        host="127.0.0.1",
        port=0,
        config=cfg,
        authenticator=authenticator,
        repository_container=repos,
    )
    server.start(block=False)
    actual_port = server._server.server_address[1]

    yield server, f"http://127.0.0.1:{actual_port}", token_a, token_b, repos

    server.stop()


def _request(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
        req.data = body

    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = {"raw": err_body}
        return e.code, parsed


class TestCognitiveMemoryApiIntegration:
    """REST API integration test suite for cognitive memory endpoints."""

    def test_unauthorized_access_rejected(self, test_server):
        server, base_url, token_a, token_b, repos = test_server
        status, data = _request(f"{base_url}/v1/cognitive-memory/query")
        assert status == 401

    def test_record_and_query_memory(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        # 1. Record Memory
        payload = {
            "content": "User prefers tabs over spaces for Python",
            "memory_type": "preference",
            "category": "coding",
            "key": "indentation",
            "confidence": 0.9,
            "provenance_type": "user_explicit",
            "tags": ["python", "formatting"],
        }
        status, data = _request(
            f"{base_url}/v1/cognitive-memory/record",
            method="POST",
            data=payload,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert status == 201
        assert "memory" in data
        mem_id = data["memory"]["memory_id"]
        assert data["memory"]["key"] == "indentation"

        # 2. Query Memories
        q_status, q_data = _request(
            f"{base_url}/v1/cognitive-memory/query?category=coding",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert q_status == 200
        assert q_data["count"] >= 1
        assert any(m["memory_id"] == mem_id for m in q_data["memories"])

        # 3. Get Memory by ID
        g_status, g_data = _request(
            f"{base_url}/v1/cognitive-memory/{mem_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert g_status == 200
        assert g_data["memory_id"] == mem_id

        # 4. Tenant B cannot access Tenant A's memory (404)
        b_status, b_data = _request(
            f"{base_url}/v1/cognitive-memory/{mem_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_status == 404

    def test_profile_put_and_get(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        # 1. Update Profile
        put_payload = {
            "preferences": {"verbosity": 3, "theme": "solarized_dark"},
            "inferred_traits": {"expertise": "expert"},
        }
        status, data = _request(
            f"{base_url}/v1/cognitive-memory/profile",
            method="PUT",
            data=put_payload,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert status == 200
        assert data["preferences"]["theme"] == "solarized_dark"

        # 2. Get Profile
        g_status, g_data = _request(
            f"{base_url}/v1/cognitive-memory/profile",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert g_status == 200
        assert g_data["preferences"]["theme"] == "solarized_dark"

    def test_feedback_endpoint_application(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        # Record initial memory
        mem, _ = repos.cognitive_memories.record_memory(
            tenant_id="user_alpha",
            content="Preferred framework is Django",
            key="framework",
            confidence=0.7,
        )

        # Send positive feedback
        fb_payload = {
            "target_memory_id": mem.memory_id,
            "feedback_type": "positive",
        }
        status, data = _request(
            f"{base_url}/v1/cognitive-memory/feedback",
            method="POST",
            data=fb_payload,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert status == 200
        assert data["feedback_event"]["applied"] is True
        assert data["updated_target_memory"]["confidence"] == 0.8

    def test_personalization_context_endpoint(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        # Populate memory
        repos.cognitive_memories.record_memory(
            tenant_id="user_alpha",
            content="PostgreSQL 16 is the target DB",
            category="architecture",
            confidence=1.0,
        )

        status, data = _request(
            f"{base_url}/v1/cognitive-memory/context?query=database",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert status == 200
        assert "[USER PERSONALIZATION & COGNITIVE CONTEXT]" in data["context"]
        assert "PostgreSQL 16" in data["context"]

    def test_patch_and_delete_memory(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        mem, _ = repos.cognitive_memories.record_memory(
            tenant_id="user_alpha",
            content="Temporary rule",
            key="temp_rule",
        )

        # Patch state
        p_status, p_data = _request(
            f"{base_url}/v1/cognitive-memory/{mem.memory_id}",
            method="PATCH",
            data={"lifecycle_state": "stale", "confidence": 0.2},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert p_status == 200
        assert p_data["lifecycle_state"] == "stale"
        assert p_data["confidence"] == 0.2

        # Delete memory
        d_status, d_data = _request(
            f"{base_url}/v1/cognitive-memory/{mem.memory_id}?hard=true",
            method="DELETE",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert d_status == 200
        assert d_data["deleted"] is True

    def test_purge_tenant_memories(self, test_server):
        server, base_url, token_a, token_b, repos = test_server

        repos.cognitive_memories.record_memory(tenant_id="user_alpha", content="Rule A")
        repos.cognitive_memories.record_memory(tenant_id="user_alpha", content="Rule B")

        status, data = _request(
            f"{base_url}/v1/cognitive-memory/purge",
            method="DELETE",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert status == 200
        assert data["purged_count"] >= 2
        assert data["success"] is True
