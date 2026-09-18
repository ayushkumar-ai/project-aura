"""M51 Multi-Provider Model Gateway Factory.

Constructs ModelGateway instances configured with primary and fallback providers
(Groq, OpenRouter, Mistral, Gemini, OpenAI, Generic/Local, Fake) based on application Settings.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.config import Settings, settings
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.model_gateway import (
    CostMetadata,
    ModelGateway,
    PricingMode,
    ProviderCatalog,
    ProviderRegistration,
)
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.generic_provider import GenericOpenAICompatibleProvider
from providers.openai_model import OpenAIProvider

logger = logging.getLogger("aura.gateway_factory")


def _get_default_model_metadata(provider_name: str, model_name: str) -> tuple[set[str], int, int, CostMetadata]:
    """Return default capabilities, context window, max tokens, and cost profile for a provider/model pair."""
    p_name = provider_name.strip().lower()
    m_name = model_name.strip().lower()

    if p_name == "groq":
        caps = {"tool_calling", "structured_output", "reasoning", "general_chat", "low_latency"}
        ctx = 131072 if "120b" in m_name else 32768
        out = 4096
        cost = CostMetadata(
            input_cost_per_million=0.15,
            output_cost_per_million=0.60,
            pricing_mode=PricingMode.PAID,
            pricing_source="groq_default",
        )
        return caps, ctx, out, cost

    if p_name == "openrouter":
        caps = {"tool_calling", "structured_output", "reasoning", "general_chat"}
        ctx = 131072 if "120b" in m_name else 32768
        out = 4096
        if ":free" in m_name:
            cost = CostMetadata(
                input_cost_per_million=0.0,
                output_cost_per_million=0.0,
                pricing_mode=PricingMode.FREE,
                pricing_source="openrouter_free",
            )
        else:
            cost = CostMetadata(
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                pricing_mode=PricingMode.PAID,
                pricing_source="openrouter_paid",
            )
        return caps, ctx, out, cost

    if p_name == "mistral":
        caps = {"tool_calling", "structured_output", "general_chat"}
        ctx = 32768
        out = 4096
        cost = CostMetadata(
            input_cost_per_million=0.20,
            output_cost_per_million=0.60,
            pricing_mode=PricingMode.PAID,
            pricing_source="mistral_default",
        )
        return caps, ctx, out, cost

    if p_name == "gemini":
        caps = {"tool_calling", "structured_output", "reasoning", "multimodal", "general_chat", "low_latency"}
        ctx = 1048576
        out = 8192
        cost = CostMetadata(
            input_cost_per_million=0.075,
            output_cost_per_million=0.30,
            pricing_mode=PricingMode.PAID,
            pricing_source="gemini_default",
        )
        return caps, ctx, out, cost

    if p_name == "openai":
        caps = {"tool_calling", "structured_output", "multimodal", "general_chat", "low_latency"}
        ctx = 128000
        out = 16384
        cost = CostMetadata(
            input_cost_per_million=0.15,
            output_cost_per_million=0.60,
            pricing_mode=PricingMode.PAID,
            pricing_source="openai_default",
        )
        return caps, ctx, out, cost

    if p_name in ("", "fake"):
        caps = {"tool_calling", "structured_output", "reasoning", "multimodal", "general_chat", "low_latency"}
        ctx = 32768
        out = 2048
        cost = CostMetadata(
            input_cost_per_million=0.0,
            output_cost_per_million=0.0,
            pricing_mode=PricingMode.FREE,
            pricing_source="fake_default",
        )
        return caps, ctx, out, cost

    # Generic / other
    caps = {"general_chat"}
    ctx = 32768
    out = 4096
    cost = CostMetadata(pricing_mode=PricingMode.UNKNOWN)
    return caps, ctx, out, cost


def _build_provider_instance(
    provider_name: str,
    model_name: str = "",
    api_key: str = "",
    base_url: str = "",
    custom_headers: dict[str, str] | None = None,
    timeout: float | None = None,
    allow_local: bool = True,
) -> ModelInterface:
    """Instantiate a single ModelInterface provider by name and configuration."""
    p_name = provider_name.strip().lower()

    if p_name in ("", "fake"):
        return FakeModelProvider()

    if p_name == "gemini":
        eff_url = base_url.strip() if base_url.strip() else "https://generativelanguage.googleapis.com/v1beta/openai/"
        eff_model = model_name.strip() if model_name.strip() else "gemini-2.5-flash"
        return GenericOpenAICompatibleProvider(
            base_url=eff_url,
            model_name=eff_model,
            api_key=api_key,
            custom_headers=custom_headers,
            timeout=timeout,
            allow_local_endpoints=allow_local,
        )

    if p_name == "groq":
        eff_url = base_url.strip() if base_url.strip() else "https://api.groq.com/openai/v1"
        eff_model = model_name.strip() if model_name.strip() else "openai/gpt-oss-120b"
        return GenericOpenAICompatibleProvider(
            base_url=eff_url,
            model_name=eff_model,
            api_key=api_key,
            custom_headers=custom_headers,
            timeout=timeout,
            allow_local_endpoints=allow_local,
        )

    if p_name == "openrouter":
        eff_url = base_url.strip() if base_url.strip() else "https://openrouter.ai/api/v1"
        eff_model = model_name.strip() if model_name.strip() else "openai/gpt-oss-120b:free"
        headers = dict(custom_headers or {})
        if "HTTP-Referer" not in headers:
            headers["HTTP-Referer"] = "https://github.com/project-aura"
        if "X-Title" not in headers:
            headers["X-Title"] = "Project AURA"
        return GenericOpenAICompatibleProvider(
            base_url=eff_url,
            model_name=eff_model,
            api_key=api_key,
            custom_headers=headers,
            timeout=timeout,
            allow_local_endpoints=allow_local,
        )

    if p_name == "mistral":
        eff_url = base_url.strip() if base_url.strip() else "https://api.mistral.ai/v1"
        eff_model = model_name.strip() if model_name.strip() else "mistral-small-latest"
        return GenericOpenAICompatibleProvider(
            base_url=eff_url,
            model_name=eff_model,
            api_key=api_key,
            custom_headers=custom_headers,
            timeout=timeout,
            allow_local_endpoints=allow_local,
        )

    if p_name == "openai":
        eff_model = model_name.strip() if model_name.strip() else "gpt-4o-mini"
        if base_url.strip() and base_url.strip() != "https://api.openai.com/v1":
            return GenericOpenAICompatibleProvider(
                base_url=base_url.strip(),
                model_name=eff_model,
                api_key=api_key,
                custom_headers=custom_headers,
                timeout=timeout,
                allow_local_endpoints=allow_local,
            )
        eff_key = api_key.strip() if api_key.strip() else "not-provided"
        return OpenAIProvider(
            model_name=eff_model,
            api_key=eff_key,
            timeout=timeout,
        )

    if p_name in ("generic", "openai_compatible", "local", "ollama", "vllm", "lmstudio"):
        if not base_url.strip():
            raise ValueError(f"Base URL cannot be empty for generic provider '{provider_name}'.")
        eff_model = model_name.strip() if model_name.strip() else "default-model"
        return GenericOpenAICompatibleProvider(
            base_url=base_url,
            model_name=eff_model,
            api_key=api_key,
            custom_headers=custom_headers,
            timeout=timeout,
            allow_local_endpoints=allow_local,
        )

    raise ValueError(f"Unsupported model provider: {provider_name}")


def create_model_gateway(
    config: Settings | None = None,
    custom_registrations: Sequence[ProviderRegistration] | None = None,
) -> ModelGateway:
    """Create and wire a ModelGateway from application settings or custom registrations."""
    cfg = config or settings

    if custom_registrations:
        catalog = ProviderCatalog(custom_registrations)
        return ModelGateway(
            catalog=catalog,
            fallback_enabled=cfg.aura_model_fallback_enabled,
            max_fallback_attempts=cfg.aura_max_model_fallback_attempts,
            retry_on_rate_limit=cfg.aura_gateway_retry_on_rate_limit,
            routing_strategy=getattr(cfg, "aura_model_routing_strategy", "priority"),
        )

    registrations: list[ProviderRegistration] = []
    cb_cfg = CircuitBreakerConfig(
        failure_threshold=int(getattr(cfg, "aura_circuit_breaker_failure_threshold", 5)),
        recovery_timeout_seconds=float(getattr(cfg, "aura_circuit_breaker_recovery_timeout_seconds", 30.0)),
    )

    primary_name = (cfg.aura_model_provider or "fake").strip().lower()

    # 1. Primary Provider Configuration
    primary_model = (
        cfg.aura_model_name
        or cfg.aura_generic_model_name
        or (cfg.aura_gemini_model_name if primary_name == "gemini" else "")
        or (cfg.aura_groq_model_name if primary_name == "groq" else "")
        or (cfg.aura_openrouter_model_name if primary_name == "openrouter" else "")
        or (cfg.aura_mistral_model_name if primary_name == "mistral" else "")
        or (cfg.aura_openai_model_name if primary_name == "openai" else "")
        or "fake-model-v1"
    )
    primary_key = (
        cfg.aura_api_key
        or cfg.aura_generic_model_api_key
        or (cfg.aura_gemini_api_key if primary_name == "gemini" else "")
        or (cfg.aura_groq_api_key if primary_name == "groq" else "")
        or (cfg.aura_openrouter_api_key if primary_name == "openrouter" else "")
        or (cfg.aura_mistral_api_key if primary_name == "mistral" else "")
        or (cfg.aura_openai_api_key if primary_name == "openai" else "")
    )
    primary_url = (
        cfg.aura_generic_model_endpoint_url
        or cfg.aura_local_model_endpoint_url
        or (cfg.aura_gemini_endpoint_url if primary_name == "gemini" else "")
        or (cfg.aura_groq_endpoint_url if primary_name == "groq" else "")
        or (cfg.aura_openrouter_endpoint_url if primary_name == "openrouter" else "")
        or (cfg.aura_mistral_endpoint_url if primary_name == "mistral" else "")
        or (cfg.aura_openai_endpoint_url if primary_name == "openai" else "")
    )

    primary_inst = _build_provider_instance(
        provider_name=primary_name,
        model_name=primary_model,
        api_key=primary_key,
        base_url=primary_url,
        timeout=cfg.aura_model_request_timeout_seconds,
        allow_local=cfg.aura_allow_local_model_endpoints,
    )

    p_caps, p_ctx, p_out, p_cost = _get_default_model_metadata(primary_name, primary_model)

    registrations.append(
        ProviderRegistration(
            provider_id=primary_name,
            provider=primary_inst,
            priority=10,
            capabilities=p_caps,
            context_window=p_ctx,
            max_output_tokens=p_out,
            cost_metadata=p_cost,
            circuit_breaker=CircuitBreaker(name=primary_name, config=cb_cfg),
            timeout_seconds=cfg.aura_model_request_timeout_seconds,
            is_fallback=False,
            model_name=primary_model,
        )
    )

    # 2. Fallback Providers
    fallback_names_raw = (cfg.aura_model_fallback_providers or "").strip()
    if fallback_names_raw and cfg.aura_model_fallback_enabled:
        fallback_names = [f.strip().lower() for f in fallback_names_raw.split(",") if f.strip()]
        fb_priority = 20

        for fb_name in fallback_names:
            if fb_name == primary_name:
                continue

            fb_model = ""
            fb_key = ""
            fb_url = ""

            if fb_name == "gemini":
                fb_model = cfg.aura_gemini_model_name
                fb_key = cfg.aura_gemini_api_key or cfg.aura_api_key
                fb_url = cfg.aura_gemini_endpoint_url
            elif fb_name == "groq":
                fb_model = cfg.aura_groq_model_name
                fb_key = cfg.aura_groq_api_key
                fb_url = cfg.aura_groq_endpoint_url
            elif fb_name == "openrouter":
                fb_model = cfg.aura_openrouter_model_name
                fb_key = cfg.aura_openrouter_api_key
                fb_url = cfg.aura_openrouter_endpoint_url
            elif fb_name == "mistral":
                fb_model = cfg.aura_mistral_model_name
                fb_key = cfg.aura_mistral_api_key
                fb_url = cfg.aura_mistral_endpoint_url
            elif fb_name == "openai":
                fb_model = cfg.aura_openai_model_name
                fb_key = cfg.aura_openai_api_key or cfg.aura_api_key
                fb_url = cfg.aura_openai_endpoint_url
            elif fb_name == "generic":
                fb_model = cfg.aura_generic_model_name
                fb_key = cfg.aura_generic_model_api_key
                fb_url = cfg.aura_generic_model_endpoint_url
            elif fb_name == "fake":
                fb_model = "fake-model-v1"

            try:
                fb_inst = _build_provider_instance(
                    provider_name=fb_name,
                    model_name=fb_model,
                    api_key=fb_key,
                    base_url=fb_url,
                    timeout=cfg.aura_model_request_timeout_seconds,
                    allow_local=cfg.aura_allow_local_model_endpoints,
                )
                fb_caps, fb_ctx, fb_out, fb_cost = _get_default_model_metadata(fb_name, fb_model)

                registrations.append(
                    ProviderRegistration(
                        provider_id=fb_name,
                        provider=fb_inst,
                        priority=fb_priority,
                        capabilities=fb_caps,
                        context_window=fb_ctx,
                        max_output_tokens=fb_out,
                        cost_metadata=fb_cost,
                        circuit_breaker=CircuitBreaker(name=fb_name, config=cb_cfg),
                        timeout_seconds=cfg.aura_model_request_timeout_seconds,
                        is_fallback=True,
                        model_name=fb_model,
                    )
                )
                fb_priority += 10
            except Exception as e:
                logger.warning("Failed to initialize fallback provider '%s': %s", fb_name, e)

    catalog = ProviderCatalog(registrations)
    return ModelGateway(
        catalog=catalog,
        fallback_enabled=cfg.aura_model_fallback_enabled,
        max_fallback_attempts=cfg.aura_max_model_fallback_attempts,
        retry_on_rate_limit=cfg.aura_gateway_retry_on_rate_limit,
        routing_strategy=getattr(cfg, "aura_model_routing_strategy", "priority"),
    )
