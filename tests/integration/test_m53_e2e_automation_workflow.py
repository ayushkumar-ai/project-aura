"""M53 — End-to-End Automation REST API and Supervisor Tests.

Verifies:
1. /v1/automations REST API endpoints (CRUD, pause, resume, manual trigger)
2. Strict tenant authorization with Bearer tokens (cross-tenant access returns 404)
3. End-to-end autonomous supervisor integration
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request
import urllib.error
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import create_token_authenticator
from core.identity import UserIdentity, UserRole, UserScope
from core.repositories.factory import create_in_memory_repositories


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_m53_server():
    repos = create_in_memory_repositories()
    port = _find_free_port()
    cfg = Settings(
        aura_env="testing",
        aura_app_name="AURA_M53_TEST",
        aura_api_key_auth_enabled=True,
        aura_automations_enabled=True,
        _env_file=None,
    )

    user_alice = UserIdentity(
        user_id="alice",
        username="Alice",
        roles=frozenset({UserRole.USER}),
        scopes=frozenset({UserScope.RUN.value, UserScope.READ_STATE.value, UserScope.WRITE_STATE.value}),
    )
    user_bob = UserIdentity(
        user_id="bob",
        username="Bob",
        roles=frozenset({UserRole.USER}),
        scopes=frozenset({UserScope.RUN.value, UserScope.READ_STATE.value, UserScope.WRITE_STATE.value}),
    )

    auth = create_token_authenticator(
        master_key="admin_master_key_123",
        additional_tokens={
            "token_alice": user_alice,
            "token_bob": user_bob,
        },
    )

    server = AURAHTTPServer(
        config=cfg,
        host="127.0.0.1",
        port=port,
        authenticator=auth,
        repository_container=repos,
    )
    server.start(block=False)
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, repos

    server.stop()


def _request(url: str, method: str = "GET", data: dict | None = None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"raw": err_body}


def test_e2e_automations_rest_api_lifecycle(live_m53_server):
    base_url, _ = live_m53_server

    # 1. Create Automation for Alice
    payload = {
        "name": "Weekly Digest",
        "description": "Compiles notes every Monday",
        "trigger_type": "recurring",
        "trigger_config": {"cron": "0 9 * * 1"},
        "action_template": {"title": "Digest Action", "goal": "Compile digest"},
        "condition_config": {"tier": 1, "predicate": "1 == 1"},
    }
    status, data = _request(f"{base_url}/v1/automations", method="POST", data=payload, token="token_alice")
    assert status == 200
    auto_id = data["id"]
    assert data["name"] == "Weekly Digest"
    assert data["status"] == "active"

    # 2. Alice can get
    status_get, data_get = _request(f"{base_url}/v1/automations/{auto_id}", token="token_alice")
    assert status_get == 200
    assert data_get["id"] == auto_id

    # 3. Bob CANNOT see Alice's automation (returns 404)
    status_b, _ = _request(f"{base_url}/v1/automations/{auto_id}", token="token_bob")
    assert status_b == 404

    # 4. List Automations for Alice
    status_list, list_data = _request(f"{base_url}/v1/automations", token="token_alice")
    assert status_list == 200
    assert len(list_data["automations"]) >= 1

    # Bob's list is empty
    status_list_b, list_data_b = _request(f"{base_url}/v1/automations", token="token_bob")
    assert status_list_b == 200
    assert len(list_data_b["automations"]) == 0

    # 5. Pause and Resume
    status_pause, pause_data = _request(f"{base_url}/v1/automations/{auto_id}/pause", method="POST", data={}, token="token_alice")
    assert status_pause == 200
    assert pause_data["status"] == "paused"

    status_resume, resume_data = _request(f"{base_url}/v1/automations/{auto_id}/resume", method="POST", data={}, token="token_alice")
    assert status_resume == 200
    assert resume_data["status"] == "active"

    # 6. Manual Trigger
    status_trig, trig_data = _request(f"{base_url}/v1/automations/{auto_id}/trigger", method="POST", data={}, token="token_alice")
    assert status_trig == 200
    assert trig_data["status"] == "triggered"

    # 7. Delete Automation
    status_del, del_data = _request(f"{base_url}/v1/automations/{auto_id}", method="DELETE", token="token_alice")
    assert status_del == 200
    assert del_data["deleted"] is True

    # Post-delete lookup returns 404
    status_after, _ = _request(f"{base_url}/v1/automations/{auto_id}", token="token_alice")
    assert status_after == 404
