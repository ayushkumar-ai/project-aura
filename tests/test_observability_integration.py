"""End-to-End Integration Tests for Production Observability Fabric (M44)."""

import json
import time
import urllib.request
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.metrics import get_metrics_registry


def test_http_observability_full_lifecycle():
    metrics = get_metrics_registry()
    metrics.reset_all()

    config = Settings()
    config.aura_server_port = 8899
    config.aura_api_key_auth_enabled = False  # dev mode

    server = AURAHTTPServer(config=config, host="127.0.0.1", port=8899)
    server.start(block=False)
    time.sleep(0.3)

    test_request_id = "req_e2e_obs_778899"
    test_traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    try:
        # 1. POST /v1/run with custom correlation headers
        payload = json.dumps({"prompt": "Echo status check"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8899/v1/run",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": test_request_id,
                "traceparent": test_traceparent,
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            headers = dict(resp.headers)
            # Response headers must return correlation identifiers
            assert headers.get("X-Request-ID") == test_request_id
            assert headers.get("X-Trace-ID") == "4bf92f3577b34da6a3ce929d0e0e4736"

            data = json.loads(resp.read().decode("utf-8"))
            assert "content" in data
            assert "timestamp" in data

        # 2. Scrape Prometheus /metrics endpoint
        metrics_req = urllib.request.Request("http://127.0.0.1:8899/metrics")
        with urllib.request.urlopen(metrics_req) as m_resp:
            assert m_resp.status == 200
            content_type = m_resp.headers.get("Content-Type", "")
            assert "text/plain" in content_type
            prom_body = m_resp.read().decode("utf-8")

            # Check that HTTP metrics recorded the POST /v1/run request
            assert "aura_http_requests_total" in prom_body
            assert 'path="/v1/run"' in prom_body
            assert 'method="POST"' in prom_body
            assert 'status_code="200"' in prom_body

    finally:
        server.stop()
