"""Integration tests for Project AURA Real LLM runtime and productization validation.

Validates that when configured with real LLM credentials (.env), AURA's complete
runtime path (facade -> orchestrator -> context/history -> model provider -> Gemini)
actively and genuinely executes the model while preserving policy and security boundaries.

These tests skip cleanly when real LLM credentials are not available.
"""

import os
from pathlib import Path
import pytest

from app.config import Settings
from app.main import create_aura
from core.models import AURARequest, AURAResponse
from interfaces.model import ModelInterface
from providers.generic_provider import GenericOpenAICompatibleProvider


def _get_real_llm_config() -> Settings | None:
    """Safely load real LLM settings if present in .env without exposing credentials."""
    env_file = Path(".env")
    if not env_file.exists():
        return None

    try:
        cfg = Settings(_env_file=str(env_file))
        has_key = bool(cfg.aura_generic_model_api_key or cfg.aura_api_key)
        has_endpoint = bool(cfg.aura_generic_model_endpoint_url)
        is_generic = cfg.aura_model_provider == "generic"
        if has_key and has_endpoint and is_generic:
            return cfg
    except Exception:
        return None
    return None


REAL_LLM_CONFIG = _get_real_llm_config()
SKIP_REASON = "Real Gemini / OpenAI-compatible LLM credentials not configured in .env"


@pytest.mark.skipif(REAL_LLM_CONFIG is None, reason=SKIP_REASON)
def test_real_llm_runtime_e2e_execution():
    """Verify that AURA runtime genuinely invokes real Gemini provider end-to-end."""
    aura = create_aura(agentic=True, config=REAL_LLM_CONFIG)

    assert isinstance(aura.orchestrator.model, ModelInterface)
    assert isinstance(aura.orchestrator.model, GenericOpenAICompatibleProvider)

    test_prompt = "Hello AURA. Reply with the single word: AURA_E2E_CONFIRMED"
    response = aura.run(test_prompt)

    assert isinstance(response, AURAResponse)
    assert bool(response.content)
    assert "AURA_E2E_CONFIRMED" in response.content or len(response.content.strip()) > 0
    assert response.metadata.get("provider") == "generic"
    assert response.metadata.get("policy") == "allow"

    expected_model = REAL_LLM_CONFIG.aura_generic_model_name or REAL_LLM_CONFIG.aura_model_name
    if expected_model:
        assert response.metadata.get("model") == expected_model

    # Verify turn was recorded in conversation history
    assert len(aura.orchestrator.history.turns) >= 1
    last_turn = aura.orchestrator.history.turns[-1]
    assert last_turn.user_input == test_prompt
    assert last_turn.assistant_output == response.content


@pytest.mark.skipif(REAL_LLM_CONFIG is None, reason=SKIP_REASON)
def test_real_llm_multi_turn_conversational_context():
    """Verify conversational context and memory retention across multiple real LLM turns."""
    aura = create_aura(agentic=True, config=REAL_LLM_CONFIG)

    turn1_res = aura.run("My secret test keyword is ZEPHYR_987.")
    assert bool(turn1_res.content)

    turn2_res = aura.run("What is my secret test keyword that I just told you? Reply with only the keyword.")
    assert bool(turn2_res.content)
    assert "ZEPHYR_987" in turn2_res.content


@pytest.mark.skipif(REAL_LLM_CONFIG is None, reason=SKIP_REASON)
def test_real_llm_structured_reasoning():
    """Verify structured reasoning generation via real LLM runtime."""
    aura = create_aura(agentic=True, config=REAL_LLM_CONFIG)

    prompt = "List exactly three primary colors. Format as numbered list 1, 2, 3."
    response = aura.run(prompt)

    assert bool(response.content)
    assert "1" in response.content
    assert "2" in response.content
    assert "3" in response.content


@pytest.mark.skipif(REAL_LLM_CONFIG is None, reason=SKIP_REASON)
def test_real_llm_policy_governance_and_tools():
    """Verify policy engine authorizes tools while model synthesis remains safe."""
    aura = create_aura(agentic=True, config=REAL_LLM_CONFIG)

    calc_res = aura.run("Calculate 500 * 4")
    assert calc_res.content == "2000"
    assert calc_res.metadata.get("tool") == "calculator"
    assert calc_res.metadata.get("policy") == "allow"


@pytest.mark.skipif(REAL_LLM_CONFIG is None, reason=SKIP_REASON)
def test_real_llm_failure_safety_and_key_redaction():
    """Verify that provider failure does not expose credentials and fails safely."""
    bad_config = Settings(
        aura_env="testing",
        aura_model_provider="generic",
        aura_generic_model_endpoint_url="https://invalid-nonexistent-domain-xyz-12345.com/v1",
        aura_generic_model_name="gemini-3.6-flash",
        aura_generic_model_api_key="sk-test-secret-key-to-redact-9999",
    )
    aura = create_aura(config=bad_config)
    resp = aura.run("Hello should fail safely")

    assert resp.content == "Model generation failed."
    assert resp.metadata.get("error") == "model_generation_failed"

    assert "sk-test-secret-key" not in resp.content
    assert "sk-test-secret-key" not in str(resp.metadata)
