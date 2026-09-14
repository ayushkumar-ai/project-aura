"""Tests for Health, Readiness Probes & Lifecycle Reliability (M44)."""

import json
import time
import urllib.request
import pytest

from app.config import Settings
from app.server import AURAHTTPServer


def test_health_liveness_probe():
    config = Settings()
    config.aura_server_port = 8891
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=8891)
    server.start(block=False)
    time.sleep(0.3)

    try:
        req = urllib.request.Request("http://127.0.0.1:8891/health")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "healthy"
            assert "uptime_seconds" in data
            assert data["environment"] == config.aura_env
    finally:
        server.stop()


def test_ready_probe_healthy():
    config = Settings()
    config.aura_server_port = 8892
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=8892)
    server.start(block=False)
    time.sleep(0.3)

    try:
        req = urllib.request.Request("http://127.0.0.1:8892/ready")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ready"
            assert data["ready"] is True
    finally:
        server.stop()


def test_ready_probe_fails_closed_on_db_disconnect_in_production():
    config = Settings()
    config.aura_server_port = 8893

    server = AURAHTTPServer(config=config, host="127.0.0.1", port=8893)
    server.start(block=False)
    time.sleep(0.3)

    try:
        # Simulate production environment and disconnected DB pool on server repository container
        server.config.aura_env = "production"
        server.config.aura_database_url = "postgresql://db:5432/aura"

        class MockDisconnectedPool:
            def check_health(self):
                return {"connected": False, "error": "ConnectionRefusedError: DB host down"}

        class MockContainer:
            db_pool = MockDisconnectedPool()

        server._server.repository_container = MockContainer()

        req = urllib.request.Request("http://127.0.0.1:8893/ready")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)

        assert exc_info.value.code == 503
        err_body = json.loads(exc_info.value.read().decode("utf-8"))
        assert "not_ready" in err_body["error"]["code"]
    finally:
        server.stop()

