import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
from app.config import Settings
from app.server import AURAHTTPServer
from core.identity import UserIdentity, UserRole, UserScope
from core.auth import create_token_authenticator


def _http_request(url: str, method: str = "GET", data: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    raw_body = json.dumps(data).encode("utf-8") if data is not None else None
    req = Request(url, data=raw_body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=5) as response:
            resp_body = response.read().decode("utf-8")
            return response.status, json.loads(resp_body) if resp_body else {}
    except HTTPError as e:
        resp_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(resp_body)
        except Exception:
            parsed = {"raw": resp_body}
        return e.code, parsed


def test_m41_http_auth_and_user_isolation():
    config = Settings(
        aura_api_key_auth_enabled=True,
        aura_server_host="127.0.0.1",
        aura_server_port=8941,
        aura_env="production",
    )

    admin_ident = UserIdentity(
        user_id="admin_user",
        username="Admin",
        roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scopes=frozenset(s.value for s in UserScope),
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
        master_key="admin_secret_token_123",
        additional_tokens={
            "alice_token_secret": user_alice,
            "bob_token_secret": user_bob,
        },
    )

    server = AURAHTTPServer(config=config, authenticator=auth)
    server.start(block=False)
    time.sleep(0.5)

    base_url = "http://127.0.0.1:8941"

    try:
        # 1. Unauthenticated /health is allowed
        status, res = _http_request(f"{base_url}/health")
        assert status == 200
        assert res["status"] == "healthy"

        # 2. Missing token on protected route -> 401
        status, res = _http_request(f"{base_url}/v1/preferences", method="GET")
        assert status == 401
        assert res["error"]["code"] == "unauthorized"

        # 3. Invalid token -> 401
        status, res = _http_request(f"{base_url}/v1/preferences", method="GET", token="invalid_token")
        assert status == 401

        # 4. Valid Alice token -> 200 and isolated preferences
        status, res = _http_request(
            f"{base_url}/v1/preferences",
            method="POST",
            data={"preferred_name": "Alice In Wonderland", "verbosity": 1},
            token="alice_token_secret",
        )
        assert status == 200
        assert res["preferred_name"] == "Alice In Wonderland"

        # 5. Valid Bob token -> 200 and isolated preferences
        status, res = _http_request(
            f"{base_url}/v1/preferences",
            method="POST",
            data={"preferred_name": "Bob The Builder", "verbosity": 4},
            token="bob_token_secret",
        )
        assert status == 200
        assert res["preferred_name"] == "Bob The Builder"

        # Verify Alice's preferences remain isolated
        status, res_alice = _http_request(f"{base_url}/v1/preferences", method="GET", token="alice_token_secret")
        assert status == 200
        assert res_alice["preferred_name"] == "Alice In Wonderland"

        # 6. Scope check: Non-admin Alice accessing /v1/release/validation -> 403
        status, res_denied = _http_request(f"{base_url}/v1/release/validation", method="GET", token="alice_token_secret")
        assert status == 403
        assert res_denied["error"]["code"] == "forbidden"

        # 7. Admin accessing /v1/release/validation -> 200
        status, res_admin = _http_request(f"{base_url}/v1/release/validation", method="GET", token="admin_secret_token_123")
        assert status == 200
        assert "is_production_ready" in res_admin

        # 8. POST /v1/run as Alice
        status, res_run = _http_request(
            f"{base_url}/v1/run",
            method="POST",
            data={"user_input": "Hello AURA"},
            token="alice_token_secret",
        )
        assert status == 200
        assert "request_id" in res_run

    finally:
        server.stop()
