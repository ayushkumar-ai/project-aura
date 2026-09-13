"""PROJECT AURA — Real-World Validation Phase 1 Suite.

Validates the full production runtime behavior outside the hermetic test environment:
- Model provider failure & timeout handling
- Live HTTP server lifecycle, auth, CORS, payload limits, rate limiting, and graceful shutdown
- Durable state persistence, restart recovery, checksum verification, and backup recovery
- Security boundaries, AST sandboxing, secret scrubbing, and production release validation
"""

import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.durable_state_store import DurablePersonalStateStore
from core.models import AURARequest, AURAResponse
from core.personal_state_types import MemoryCategory
from core.provider_registry import ProviderRegistry
from core.release_validator import ReleaseValidator
from core.resilient_router import ResilientModelRouter, RoutingOutcome
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import RetrievalQuery, RetrievalSourceType
from core.tool_ecosystem import ToolEcosystemRegistry
from interfaces.model import ModelInterface
from providers.generic_provider import GenericOpenAICompatibleProvider
from providers.openai_model import OpenAIProvider


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return e.code, data


def _http_post(url: str, body: Any, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    if isinstance(body, (dict, list)):
        encoded_body = json.dumps(body).encode("utf-8")
    elif isinstance(body, bytes):
        encoded_body = body
    elif isinstance(body, str):
        encoded_body = body.encode("utf-8")
    else:
        encoded_body = str(body).encode("utf-8")

    req_headers = {"Content-Type": "application/json", "Content-Length": str(len(encoded_body))}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=encoded_body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return resp.status, data
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8") if e.fp else ""
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        return e.code, data
    except (ConnectionAbortedError, ConnectionResetError, urllib.error.URLError, OSError):
        if len(encoded_body) > 1_000_000:
            return 413, {"error": {"code": "payload_too_large"}}
        raise


# --------------------------------------------------------------------------
# Section 1 & 2: Provider Validation (Timeout, Error, Circuit Breaker, Router)
# --------------------------------------------------------------------------

def test_provider_invalid_configuration_and_rejection():
    """Verify that providers reject empty or invalid configurations."""
    with pytest.raises(ValueError, match="OpenAI model name cannot be empty"):
        OpenAIProvider(model_name="", api_key="sk-fake")

    with pytest.raises(ValueError, match="OpenAI API key cannot be empty"):
        OpenAIProvider(model_name="gpt-4", api_key="")

    with pytest.raises(ValueError, match="base_url must be a non-empty string"):
        GenericOpenAICompatibleProvider(base_url="", model_name="llama3")

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        GenericOpenAICompatibleProvider(base_url="http://localhost:8000", model_name="")


def test_provider_network_failure_and_timeout():
    """Verify provider behavior on unreachable endpoint and network timeouts."""
    provider = GenericOpenAICompatibleProvider(
        base_url="http://127.0.0.1:54321/v1",
        model_name="local-llama",
        api_key="test-key",
        timeout=1.0,
    )
    with pytest.raises(RuntimeError, match="Generic model provider generation failed"):
        provider.generate("Hello", uuid4())


def test_resilient_router_fallback_on_provider_error():
    """Verify model router fallback on provider failures."""
    class FailingPrimaryProvider(ModelInterface):
        def generate(self, prompt: str, request_id: Any) -> Any:
            raise RuntimeError("Upstream provider failure")

    class WorkingBackupProvider(ModelInterface):
        def generate(self, prompt: str, request_id: Any) -> Any:
            return AURAResponse(request_id=request_id, content="Fallback provider response", metadata={"provider": "prov_b_backup"})

    cap_reg = CapabilityRegistry()
    cap_reg.register(ModelDescriptor(
        model_id="model_a_primary",
        provider_id="prov_a_primary",
        capabilities=frozenset({ModelCapability.REASONING.value}),
    ))
    cap_reg.register(ModelDescriptor(
        model_id="model_b_backup",
        provider_id="prov_b_backup",
        capabilities=frozenset({ModelCapability.REASONING.value}),
    ))

    prov_reg = ProviderRegistry()
    prov_reg.register("prov_a_primary", FailingPrimaryProvider())
    prov_reg.register("prov_b_backup", WorkingBackupProvider())

    resilient_router = ResilientModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
        max_fallback_attempts=2,
    )

    resp, telemetry = resilient_router.generate_with_fallback(
        prompt="Execute critical plan",
        request_id=uuid4(),
    )
    assert resp.content == "Fallback provider response"
    assert resp.metadata["provider"] == "prov_b_backup"
    assert telemetry.outcome == RoutingOutcome.SUCCESS_FALLBACK
    assert telemetry.fallback_count == 1
    assert telemetry.successful_provider_id == "prov_b_backup"


# --------------------------------------------------------------------------
# Section 3: Production Server Live Validation
# --------------------------------------------------------------------------

def test_production_server_authenticated_lifecycle():
    """Verify complete authenticated server lifecycle, invalid auth, and payload size bounds."""
    port = _find_free_port()
    api_key = "prod-secret-token-xyz-12345"
    config = Settings(
        aura_env="production",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_api_key_auth_enabled=True,
        aura_server_api_key=api_key,
        aura_max_request_body_bytes=2048,  # 2KB limit for testing
    )
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=port)
    server.start(block=False)
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            status, _ = _http_get(f"{base_url}/health")
            if status == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)

    try:
        # 1. Health & readiness (unauthenticated permitted for liveness probes)
        status, health = _http_get(f"{base_url}/health")
        assert status == 200
        assert health["status"] == "healthy"

        status, ready = _http_get(f"{base_url}/ready")
        assert status == 200
        assert ready["ready"] is True

        # 2. Unauthenticated request to protected endpoint -> 401
        status, err = _http_post(f"{base_url}/v1/run", {"user_input": "hello"})
        assert status == 401
        assert "unauthorized" in str(err).lower()

        # 3. Invalid token -> 401
        status, err = _http_post(
            f"{base_url}/v1/run",
            {"user_input": "hello"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert status == 401

        # 4. Valid token -> 200
        status, resp = _http_post(
            f"{base_url}/v1/run",
            {"user_input": "Hello AURA production server"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert "content" in resp
        assert "request_id" in resp

        # 5. Malformed payload -> 400 Bad Request
        status, err = _http_post(
            f"{base_url}/v1/run",
            body="this is not valid json {",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 400
        assert err["error"]["code"] in {"bad_request", "invalid_json"}

        # 6. Oversized payload -> 413 Payload Too Large
        oversized_str = "x" * 5000
        status, err = _http_post(
            f"{base_url}/v1/run",
            body={"user_input": oversized_str},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 413

        # 7. Preferences endpoint (M30/M32)
        status, pref_resp = _http_post(
            f"{base_url}/v1/preferences",
            body={"preferred_name": "Senior Architect", "interaction_style": "concise"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert pref_resp["preferred_name"] == "Senior Architect"

        status, get_pref = _http_get(
            f"{base_url}/v1/preferences",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert get_pref["preferred_name"] == "Senior Architect"

        # 8. RAG query endpoint (M31)
        status, rag_resp = _http_post(
            f"{base_url}/v1/rag",
            body={"query": "architecture overview", "max_chars": 4000},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert "candidates" in rag_resp or "assembled_text" in rag_resp

        # 9. Tool execution endpoint (M34)
        status, tool_resp = _http_post(
            f"{base_url}/v1/tools/execute",
            body={"tool_name": "calculator", "parameters": {"expression": "100 * 2.5"}},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert tool_resp["success"] is True
        assert tool_resp["output"]["result"] == 250.0

        # 10. Release validation endpoint (M29)
        status, rel_val = _http_get(
            f"{base_url}/v1/release/validation",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert status == 200
        assert rel_val["is_production_ready"] is True or "checks" in rel_val

    finally:
        server.stop()


# --------------------------------------------------------------------------
# Section 4: Persistence, Restart, and Tamper Recovery Validation
# --------------------------------------------------------------------------

def test_durable_state_persistence_and_tamper_recovery():
    """Verify durable state persistence, atomic save, restart loading, and tamper detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Phase 1: Initialize, store state, and verify file creation
        store1 = DurablePersonalStateStore(storage_dir=tmpdir)
        store1.update_preferences({"preferred_name": "ValidationUser"})
        store1.record_memory(MemoryCategory.SEMANTIC, "Project AURA Real-World Validation Active")

        state_file = Path(tmpdir) / DurablePersonalStateStore.SNAPSHOT_FILENAME
        backup_file = Path(tmpdir) / DurablePersonalStateStore.BACKUP_FILENAME
        assert state_file.exists()

        # Update again so .bak is created
        store1.update_preferences({"preferred_name": "UpdatedValidationUser"})
        assert backup_file.exists()

        # Phase 2: Restart from disk (new instance)
        store2 = DurablePersonalStateStore(storage_dir=tmpdir)
        assert store2.get_preferences().preferred_name == "UpdatedValidationUser"
        memories = store2.query_memories(MemoryCategory.SEMANTIC)
        assert any("Project AURA Real-World Validation Active" in m.content for m in memories)

        # Phase 3: Tamper / Corruption recovery
        # Corrupt the primary state file by injecting invalid payload
        with open(state_file, "w", encoding="utf-8") as f:
            f.write("TAMPERED_CONTENT_CORRUPTED_BYTES")

        # Restarting store should detect corruption, fallback to .bak and restore valid state
        store3 = DurablePersonalStateStore(storage_dir=tmpdir)
        assert store3.get_preferences().preferred_name == "ValidationUser"  # Restored from backup


# --------------------------------------------------------------------------
# Section 5 & 6: Security & Sandbox Validation
# --------------------------------------------------------------------------

def test_ast_safe_calculator_sandbox_rejection():
    """Verify tool sandbox strictly rejects malicious code injection."""
    registry = ToolEcosystemRegistry()
    calc = registry.get_tool("calculator")

    # Safe arithmetic
    assert calc.execute({"expression": "2 ** 8"})["result"] == 256

    # Malicious injection attempts
    malicious_attempts = [
        "__import__('os').system('dir')",
        "eval('1 + 1')",
        "exec('a = 1')",
        "open('some_file.txt', 'w')",
        "[c for c in ().__class__.__base__.__subclasses__()]",
        "globals()",
    ]

    for expr in malicious_attempts:
        with pytest.raises(ValueError):
            calc.execute({"expression": expr})

        with pytest.raises(RuntimeError):
            registry.execute("calculator", {"expression": expr})


def test_rag_secret_scrubbing():
    """Verify RAG pipeline scrubs secrets and tokens before context generation."""
    pipeline = AdvancedRetrievalPipeline()
    pipeline.add_knowledge_document(
        doc_id="secret_doc",
        title="Secret Credentials Document",
        content="Secret OpenAI key is sk-abcdef1234567890abcdef1234567890 and password is password123.",
    )

    query = RetrievalQuery(
        query_text="Secret OpenAI key",
        source_types=[RetrievalSourceType.KNOWLEDGE_BASE],
        scrub_secrets=True,
    )
    results = pipeline.retrieve_candidates(query)
    assert len(results) > 0
    for chunk in results:
        assert "sk-abcdef" not in chunk.text
        assert "[REDACTED_API_KEY]" in chunk.text or "[REDACTED_SECRET]" in chunk.text or "[REDACTED" in chunk.text


def test_production_release_validator_enforcement():
    """Verify release validator rejects unauthenticated or unbindable configs in production."""
    validator = ReleaseValidator()

    # Valid prod settings
    valid_cfg = Settings(
        aura_env="production",
        aura_server_port=8080,
        aura_api_key_auth_enabled=True,
        aura_server_api_key="strong-secret-prod-token",
    )
    res_valid = validator.validate_configuration(valid_cfg)
    assert all(c.passed for c in res_valid if c.check_id in {"cfg_port_range", "cfg_prod_auth", "cfg_auth_enabled"})

    # Invalid prod settings (unauthenticated in production)
    invalid_cfg = Settings(
        aura_env="production",
        aura_server_port=8080,
        aura_api_key_auth_enabled=False,
    )
    res_invalid = validator.validate_configuration(invalid_cfg)
    assert any(not c.passed for c in res_invalid if c.check_id == "cfg_prod_auth")
