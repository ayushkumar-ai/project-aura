"""M55 — Server Fleet REST API Integration Tests.

Tests HTTP endpoints for fleet status, worker listing, tenant quota configuration,
worker draining, and on-demand crash recovery sweeping with administrative authorization.
"""

from datetime import datetime, timezone
import json
import threading
import time
import urllib.request
import urllib.error
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import TokenAuthenticator
from core.identity import UserIdentity, UserRole
from core.repositories.factory import create_in_memory_repositories
from core.fleet.types import WorkerRecord, WorkerStatus


@pytest.fixture
def test_server():
    # Setup in-memory repos and admin user
    repos = create_in_memory_repositories()
    admin_user = UserIdentity(
        user_id="admin_user_m55",
        username="admin_m55",
        roles=frozenset({UserRole.ADMIN}),
        is_authenticated=True,
    )
    repos.users.save(admin_user)

    normal_user = UserIdentity(
        user_id="normal_user_m55",
        username="user_m55",
        roles=frozenset({UserRole.USER}),
        is_authenticated=True,
    )
    repos.users.save(normal_user)

    # API Tokens
    admin_token = "aura_admin_token_secret_12345"
    repos.tokens.register_token(admin_token, "admin_user_m55")

    user_token = "aura_user_token_secret_12345"
    repos.tokens.register_token(user_token, "normal_user_m55")

    authenticator = TokenAuthenticator(token_repo=repos.tokens)

    # Settings with fleet enabled and API key auth enabled
    cfg = Settings(
        aura_env="development",
        aura_api_key_auth_enabled=True,
        aura_fleet_enabled=True,
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

    yield server, f"http://127.0.0.1:{actual_port}", admin_token, user_token, repos

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


def test_get_fleet_status_admin_and_forbidden(test_server):
    """Admin can query /v1/fleet/status; non-admin is forbidden."""
    server, base_url, admin_token, user_token, repos = test_server

    # Admin request
    status_code, data = _request(
        f"{base_url}/v1/fleet/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_code == 200
    assert "total_workers" in data
    assert "status_breakdown" in data
    assert "healthy_workers" in data

    # Non-admin request -> 403 Forbidden
    u_status, u_data = _request(
        f"{base_url}/v1/fleet/status",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert u_status == 403


def test_list_fleet_workers(test_server):
    """Admin can list registered workers via /v1/fleet/workers."""
    server, base_url, admin_token, user_token, repos = test_server

    # Register worker
    repos.fleet.register_worker(WorkerRecord(
        worker_id="wkr_api_1",
        instance_id="inst_1",
        hostname="api-node",
        process_id=5000,
        incarnation_token="inc_api_1",
        status=WorkerStatus.HEALTHY,
    ))

    status_code, data = _request(
        f"{base_url}/v1/fleet/workers",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_code == 200
    workers = data.get("workers", [])
    assert any(w["worker_id"] == "wkr_api_1" for w in workers)


def test_tenant_quota_get_and_put(test_server):
    """Admin can update tenant quota and retrieve updated quota."""
    server, base_url, admin_token, user_token, repos = test_server

    # PUT quota
    status_code, data = _request(
        f"{base_url}/v1/fleet/tenants/normal_user_m55/quota",
        method="PUT",
        data={"max_active_tasks": 30, "guaranteed_slots": 5, "burst_capacity": 50},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_code == 200
    assert data["max_active_tasks"] == 30
    assert data["guaranteed_slots"] == 5

    # GET quota
    g_status, g_data = _request(
        f"{base_url}/v1/fleet/tenants/normal_user_m55/quota",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert g_status == 200
    assert g_data["max_active_tasks"] == 30


def test_worker_drain_endpoint(test_server):
    """Admin can drain worker via POST /v1/fleet/workers/{id}/drain."""
    server, base_url, admin_token, user_token, repos = test_server

    repos.fleet.register_worker(WorkerRecord(
        worker_id="wkr_drain_api",
        instance_id="inst_d",
        hostname="drain-node",
        process_id=5001,
        incarnation_token="inc_drain_api",
        status=WorkerStatus.HEALTHY,
    ))

    status_code, data = _request(
        f"{base_url}/v1/fleet/workers/wkr_drain_api/drain",
        method="POST",
        data={"reason": "Rolling upgrade"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_code == 200
    assert data["status"] == "draining"

    # Verify status changed in repo
    w = repos.fleet.get_worker("wkr_drain_api")
    assert w.status == WorkerStatus.DRAINING


def test_fleet_sweep_endpoint(test_server):
    """Admin can trigger immediate recovery sweep via POST /v1/fleet/sweep."""
    server, base_url, admin_token, user_token, repos = test_server

    status_code, data = _request(
        f"{base_url}/v1/fleet/sweep",
        method="POST",
        data={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert status_code == 200
    assert "timestamp" in data
    assert "reaped_workers" in data
    assert "recovered_leases" in data
