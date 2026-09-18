"""M51 Multi-Provider Model Gateway & Intelligent Provider Routing.

Provides a unified ModelInterface-compliant entry point for routing requests
across multiple LLM providers (Groq, OpenRouter, Mistral, Gemini, OpenAI, Generic/Local, Fake)
with priority cascading, circuit-breaker isolation, transient failure recovery,
model-specific capability matching, explicit pricing metadata, fail-closed policy
enforcement, and low-cardinality telemetry.

Formal Invariants:
- P2-M51-F01: Provider routing is gateway-only.
- P2-M51-F02: Deterministic routing order for identical input and state.
- P2-M51-F03: Disabled providers (enabled=False) are never called.
- P2-M51-F04: Open circuit breakers are skipped without dispatching network calls.
- P2-M51-F05: Transient errors (429, timeout, 5xx) trigger controlled fallback.
- P2-M51-F06: Fatal errors (bad request, invalid schema) terminate immediately with zero fallback.
- P2-M51-F07: Policy denial causes zero provider calls (strict fail-closed).
- P2-M51-F08: Authentication/Authorization failure (401/403) does not trigger blind fallback.
- P2-M51-F09: Tenant isolation is preserved across credentials, routing state, and metrics.
- P2-M51-F10: Secrets and API keys are never exposed in exceptions, metadata, or logs.
- P2-M51-F11: SSRF protection blocks cloud metadata IPs, invalid schemes, and forbidden networks.
- P2-M51-F12: Bounded retries per provider prevents multiplicative request explosions.
- P2-M51-F13: Bounded fallback cascade depth terminates after max_fallback_attempts unique providers.
- P2-M51-F14: Provider capability correctness enforces requested capability matching.
- P2-M51-F15: Model capability correctness prevents routing to models lacking required modalities.
- P2-M51-F16: Free model availability is treated as non-guaranteed with rate-limited fallback.
- P2-M51-F17: Unknown quota remains explicitly unknown (never fabricated).
- P2-M51-F18: Provider identity is recorded in response metadata.
- P2-M51-F19: Model identity is recorded in response metadata.
- P2-M51-F20: Usage telemetry is normalized into prompt, completion, and total tokens.
- P2-M51-F21: Cost calculation is metadata-driven from configuration/catalog metadata.
- P2-M51-F22: Tool calls remain under AURA security, policy, and approval authorization.
- P2-M51-F23: Multimodal trust boundary remains owned by M57 validation.
- P2-M51-F24: M59 and higher runtime layers route exclusively through ModelGateway.
- P2-M51-F25: Circuit breaker state is isolated per provider registration.
- P2-M51-F26: Concurrent routing is thread-safe.
- P2-M51-F27: Disambiguated same-model routes across distinct providers maintain isolated state.
- P2-M51-F28: Malformed provider response cannot be converted into success.
- P2-M51-F29: Fallback exhaustion terminates safely with sanitized diagnostic trace.
- P2-M51-F30: Provider configuration cannot override or weaken system security policy.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from app.config import Settings, settings
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from core.metrics import get_metrics_registry
from core.models import AURAResponse
from core.security_scrubber import sanitize_error_message, scrub_dict, scrub_string
from core.trace_types import SpanKind, SpanStatus
from core.tracing import Tracer
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.model_gateway")


class PricingMode(str, Enum):
    """Explicit pricing classification for provider and model routes."""

    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


@dataclass
class CostMetadata:
    """Structured pricing and billing metadata for a registered model route."""

    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cached_input_cost_per_million: float | None = None
    pricing_mode: PricingMode = PricingMode.UNKNOWN
    pricing_source: str = "catalog_default"
    pricing_verified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "cached_input_cost_per_million": self.cached_input_cost_per_million,
            "pricing_mode": self.pricing_mode.value,
            "pricing_source": self.pricing_source,
            "pricing_verified_at": self.pricing_verified_at,
        }

    def compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """Compute estimated USD cost from token counts."""
        if self.pricing_mode == PricingMode.FREE:
            return 0.0
        if self.input_cost_per_million is None or self.output_cost_per_million is None:
            return None
        cost = (prompt_tokens * self.input_cost_per_million / 1_000_000.0) + (
            completion_tokens * self.output_cost_per_million / 1_000_000.0
        )
        return round(cost, 6)


class FailureClassification(str, Enum):
    """Categorization of model invocation failures."""

    SUCCESS = "success"
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    CAPABILITY_MISMATCH = "capability_mismatch"
    FATAL = "fatal"
    POLICY_DENY = "policy_deny"
    AUTH_FAILURE = "auth_failure"


class FailureClassifier:
    """Classifies model errors into transient (fallback-eligible) and fatal (fail-closed) categories."""

    # Explicit strings and patterns indicating policy or security violations (fail-closed immediately)
    POLICY_DENY_PATTERNS = (
        "policy violation",
        "policy denied",
        "access denied by policy",
        "prompt injection",
        "security violation",
        "ssrf violation",
        "metadata endpoint",
        "forbidden by policy",
        "loopback endpoints are disabled",
        "private network address",
        "policy check failed",
    )

    # Auth failures (fail immediately without cascading to subsequent providers)
    AUTH_FAILURE_PATTERNS = (
        "401",
        "403",
        "unauthorized",
        "authenticationerror",
        "authentication failed",
        "invalid api key",
        "invalid_api_key",
        "permission_denied",
        "forbidden",
        "incorrect api key",
    )

    # Quota exhaustion patterns (hard billing / credit limits, transient fallback eligible)
    QUOTA_PATTERNS = (
        "insufficient_quota",
        "credits expired",
        "billing_hard_limit_reached",
        "out of credits",
        "account_deactivated",
    )

    # Rate limiting (transient fallback eligible)
    RATE_LIMIT_PATTERNS = (
        "429",
        "ratelimiterror",
        "rate limit",
        "too many requests",
        "resourceexhausted",
        "quota exceeded",
        "rate_limit_exceeded",
        "temporarily rate limited",
    )

    # Network / Timeout / Transient 5xx server errors (transient fallback eligible)
    TRANSIENT_PATTERNS = (
        "500",
        "502",
        "503",
        "504",
        "timeouterror",
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "connection error",
        "connection failed",
        "connection closed",
        "connection",
        "apiconnectionerror",
        "internalservererror",
        "serviceunavailable",
        "badgateway",
        "remotedisconnected",
        "gateway timeout",
        "server disconnected",
        "temporarily unavailable",
    )

    @classmethod
    def classify(cls, exc: Exception | str) -> FailureClassification:
        """Classify an exception or error string into a FailureClassification."""
        err_msg = str(exc).lower() if exc is not None else ""
        exc_type_name = type(exc).__name__.lower() if isinstance(exc, Exception) else ""

        # 1. Policy / Security violations (Strict Priority 1: Fail Closed)
        if (
            any(p in err_msg for p in cls.POLICY_DENY_PATTERNS)
            or "policy" in exc_type_name
            or "security" in exc_type_name
        ):
            return FailureClassification.POLICY_DENY

        # 2. Auth / Permission failures (Priority 2: Fail Closed)
        if (
            any(p in err_msg for p in cls.AUTH_FAILURE_PATTERNS)
            or "authentication" in exc_type_name
        ):
            return FailureClassification.AUTH_FAILURE

        # 3. Capability mismatch (Fatal)
        if "capability mismatch" in err_msg:
            return FailureClassification.CAPABILITY_MISMATCH

        # 4. Bad request / Schema / Invalid input errors (Fatal)
        if (
            "bad request" in err_msg
            or "badrequesterror" in exc_type_name
            or "invalid url scheme" in err_msg
            or "missing hostname" in err_msg
            or "malformed" in err_msg
        ):
            return FailureClassification.FATAL

        # 5. Quota exhaustion (Transient fallback eligible)
        if any(p in err_msg for p in cls.QUOTA_PATTERNS):
            return FailureClassification.QUOTA_EXHAUSTED

        # 6. Rate limiting (Transient fallback eligible)
        if any(p in err_msg for p in cls.RATE_LIMIT_PATTERNS) or "ratelimit" in exc_type_name:
            return FailureClassification.RATE_LIMITED

        # 7. Timeouts (Transient fallback eligible)
        if any(p in err_msg for p in ("timeout", "timed out", "timeouterror")) or "timeout" in exc_type_name:
            return FailureClassification.TIMEOUT

        # 8. Transient 5xx / Connection errors (Transient fallback eligible)
        if any(p in err_msg for p in cls.TRANSIENT_PATTERNS) or "connection" in exc_type_name:
            return FailureClassification.TRANSIENT

        # Default fallback for generic wrapped provider runtime errors
        if "generic model provider generation failed" in err_msg:
            return FailureClassification.TRANSIENT

        return FailureClassification.FATAL

    @classmethod
    def is_fallback_eligible(cls, classification: FailureClassification) -> bool:
        """Determine if a failure classification is eligible for fallback cascade."""
        return classification in (
            FailureClassification.TRANSIENT,
            FailureClassification.RATE_LIMITED,
            FailureClassification.QUOTA_EXHAUSTED,
            FailureClassification.TIMEOUT,
            FailureClassification.CIRCUIT_OPEN,
        )


@dataclass
class ProviderRegistration:
    """Descriptor and runtime handle for a registered model provider in the gateway."""

    provider_id: str
    provider: ModelInterface
    model_name: str = ""
    priority: int = 100
    weight: float = 1.0
    capabilities: set[str] = field(default_factory=set)
    context_window: int = 32768
    max_output_tokens: int = 4096
    cost_metadata: CostMetadata = field(default_factory=CostMetadata)
    rate_limit_metadata: dict[str, Any] = field(default_factory=dict)
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_reasoning: bool = False
    supports_multimodal: bool = False
    enabled: bool = True
    circuit_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker())
    timeout_seconds: float = 30.0
    is_fallback: bool = False

    def __post_init__(self):
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string.")
        self.provider_id = self.provider_id.strip().lower()
        if not isinstance(self.provider, ModelInterface):
            raise TypeError("provider must implement ModelInterface.")
        self.priority = int(self.priority)
        self.weight = float(self.weight)
        self.timeout_seconds = float(self.timeout_seconds)
        self.context_window = int(self.context_window)
        self.max_output_tokens = int(self.max_output_tokens)
        self.enabled = bool(self.enabled)
        if not self.model_name and hasattr(self.provider, "model_name"):
            self.model_name = str(getattr(self.provider, "model_name", ""))

        # Sync capability flags into capabilities set
        if isinstance(self.capabilities, (list, tuple)):
            self.capabilities = set(self.capabilities)
        if self.supports_tools:
            self.capabilities.add("tool_calling")
        if self.supports_structured_output:
            self.capabilities.add("structured_output")
        if self.supports_reasoning:
            self.capabilities.add("reasoning")
        if self.supports_multimodal:
            self.capabilities.add("multimodal")
        self.capabilities.add("general_chat")

        # Sync back boolean flags
        self.supports_tools = "tool_calling" in self.capabilities
        self.supports_structured_output = "structured_output" in self.capabilities
        self.supports_reasoning = "reasoning" in self.capabilities
        self.supports_multimodal = "multimodal" in self.capabilities

    def matches_capabilities(self, required: Sequence[str] | None) -> bool:
        """Check if this registration satisfies all required capabilities."""
        if not required:
            return True
        req_set = {str(c).strip().lower() for c in required if str(c).strip()}
        for req in req_set:
            if req == "tool_calling" and not self.supports_tools:
                return False
            elif req == "structured_output" and not self.supports_structured_output:
                return False
            elif req == "reasoning" and not self.supports_reasoning:
                return False
            elif req == "multimodal" and not self.supports_multimodal:
                return False
            elif req not in self.capabilities:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Return serialized metadata snapshot without secrets."""
        return {
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "priority": self.priority,
            "weight": self.weight,
            "capabilities": sorted(list(self.capabilities)),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "cost_metadata": self.cost_metadata.to_dict(),
            "rate_limit_metadata": scrub_dict(self.rate_limit_metadata),
            "supports_tools": self.supports_tools,
            "supports_structured_output": self.supports_structured_output,
            "supports_reasoning": self.supports_reasoning,
            "supports_multimodal": self.supports_multimodal,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "is_fallback": self.is_fallback,
            "circuit_breaker": self.circuit_breaker.get_status_dict(),
        }


class ProviderCatalog:
    """Thread-safe catalog of registered model providers supporting capability matching and sorting."""

    def __init__(self, registrations: Sequence[ProviderRegistration] | None = None):
        self._lock = threading.RLock()
        self._registrations: dict[str, ProviderRegistration] = {}
        if registrations:
            for reg in registrations:
                self.register(reg)

    def register(self, registration: ProviderRegistration) -> None:
        """Register a provider entry in the catalog."""
        if not isinstance(registration, ProviderRegistration):
            raise TypeError("registration must be an instance of ProviderRegistration.")
        with self._lock:
            self._registrations[registration.provider_id] = registration

    def get(self, provider_id: str) -> ProviderRegistration | None:
        """Look up a provider registration by ID."""
        norm_id = str(provider_id).strip().lower()
        with self._lock:
            return self._registrations.get(norm_id)

    def has(self, provider_id: str) -> bool:
        """Check if a provider ID exists in the catalog."""
        norm_id = str(provider_id).strip().lower()
        with self._lock:
            return norm_id in self._registrations

    def list_providers(self) -> list[ProviderRegistration]:
        """Return all registered providers sorted deterministically by (priority, provider_id)."""
        with self._lock:
            return sorted(
                self._registrations.values(),
                key=lambda r: (r.priority, r.provider_id),
            )

    def select_candidates(
        self,
        required_capabilities: Sequence[str] | None = None,
        routing_preference: str | None = None,
    ) -> list[ProviderRegistration]:
        """Select and deterministically order provider candidates based on capability and preference."""
        with self._lock:
            all_active = [r for r in self._registrations.values() if r.enabled]

            if not all_active:
                return []

            # 1. Capability Filtering
            if required_capabilities:
                matching = [r for r in all_active if r.matches_capabilities(required_capabilities)]
                if not matching:
                    req_list = sorted(list({str(c).strip().lower() for c in required_capabilities if str(c).strip()}))
                    raise RuntimeError(
                        f"Capability mismatch: No registered provider supports required capabilities {req_list}."
                    )
                candidates = matching
            else:
                candidates = all_active

            # 2. Deterministic Preference Sorting
            pref = (routing_preference or "priority").strip().lower()

            if pref in ("cost", "cost_sensitive"):
                # Free routes first (0), then lowest input_cost_per_million, then priority, then provider_id
                def _cost_key(r: ProviderRegistration):
                    is_free_val = 0 if r.cost_metadata.pricing_mode == PricingMode.FREE else 1
                    cost_val = r.cost_metadata.input_cost_per_million if r.cost_metadata.input_cost_per_million is not None else 999999.0
                    return (is_free_val, cost_val, r.priority, r.provider_id)

                return sorted(candidates, key=_cost_key)

            elif pref in ("latency", "low_latency"):
                # Low latency tagged providers first (0), then priority, then provider_id
                def _latency_key(r: ProviderRegistration):
                    has_low_lat = 0 if "low_latency" in r.capabilities else 1
                    return (has_low_lat, r.priority, r.provider_id)

                return sorted(candidates, key=_latency_key)

            else:
                # Default priority-based deterministic order
                return sorted(candidates, key=lambda r: (r.priority, r.provider_id))

    def get_primary(self) -> ProviderRegistration | None:
        """Get the highest-priority primary (non-fallback) provider."""
        with self._lock:
            candidates = [r for r in self.list_providers() if not r.is_fallback and r.enabled]
            if candidates:
                return candidates[0]
            all_provs = [r for r in self.list_providers() if r.enabled]
            return all_provs[0] if all_provs else None

    def get_fallbacks(self) -> list[ProviderRegistration]:
        """Get ordered list of fallback providers."""
        with self._lock:
            primary = self.get_primary()
            primary_id = primary.provider_id if primary else None
            return [r for r in self.list_providers() if r.provider_id != primary_id and r.enabled]

    def __len__(self) -> int:
        with self._lock:
            return len(self._registrations)


class ModelGateway(ModelInterface):
    """Unified Model Gateway routing requests across multiple LLM providers.
    
    Implements ModelInterface for transparent drop-in compatibility across
    Orchestrator, AgenticRuntime, TaskPlanner, and GoalReasoner.
    """

    def __init__(
        self,
        catalog: ProviderCatalog | Sequence[ProviderRegistration],
        fallback_enabled: bool = True,
        max_fallback_attempts: int = 3,
        retry_on_rate_limit: bool = True,
        classifier: FailureClassifier | None = None,
        routing_strategy: str = "priority",
    ):
        if isinstance(catalog, ProviderCatalog):
            self.catalog = catalog
        elif isinstance(catalog, Sequence):
            self.catalog = ProviderCatalog(catalog)
        else:
            raise TypeError("catalog must be a ProviderCatalog or sequence of ProviderRegistration.")

        self.fallback_enabled = bool(fallback_enabled)
        self.max_fallback_attempts = max(1, int(max_fallback_attempts))
        self.retry_on_rate_limit = bool(retry_on_rate_limit)
        self.classifier = classifier or FailureClassifier()
        self.routing_strategy = routing_strategy or "priority"
        self._lock = threading.RLock()
        self._tracer = Tracer(service_name="aura.gateway")

    @property
    def model_name(self) -> str:
        """Expose primary provider model name for backward compatibility."""
        primary = self.catalog.get_primary()
        return primary.model_name if primary else "gateway-composite"

    def get_provider(self, provider_id: str) -> ProviderRegistration | None:
        """Lookup provider registration by ID."""
        return self.catalog.get(provider_id)

    def list_providers(self) -> list[dict[str, Any]]:
        """Return list of serialized provider snapshots."""
        return [r.to_dict() for r in self.catalog.list_providers()]

    def reset_all_circuits(self) -> None:
        """Reset all provider circuit breakers."""
        for reg in self.catalog.list_providers():
            reg.circuit_breaker.reset()

    def get_health_status(self) -> dict[str, Any]:
        """Provide aggregate and per-provider gateway health diagnostics."""
        providers_snapshot = {}
        all_closed = True
        has_healthy = False

        for reg in self.catalog.list_providers():
            cb_state = reg.circuit_breaker.state
            status_str = "healthy" if cb_state == CircuitState.CLOSED else ("degraded" if cb_state == CircuitState.HALF_OPEN else "unhealthy")
            if cb_state != CircuitState.CLOSED:
                all_closed = False
            else:
                has_healthy = True

            providers_snapshot[reg.provider_id] = {
                "model_name": reg.model_name,
                "priority": reg.priority,
                "status": status_str,
                "circuit_state": cb_state.value,
                "is_fallback": reg.is_fallback,
                "pricing_mode": reg.cost_metadata.pricing_mode.value,
                "capabilities": sorted(list(reg.capabilities)),
            }

        overall_status = "healthy" if all_closed and has_healthy else ("degraded" if has_healthy else "unhealthy")

        return {
            "status": overall_status,
            "fallback_enabled": self.fallback_enabled,
            "total_providers": len(self.catalog),
            "providers": providers_snapshot,
        }

    def generate(
        self,
        prompt: str,
        request_id: UUID,
        required_capabilities: Sequence[str] | None = None,
        routing_preference: str | None = None,
        **kwargs: Any,
    ) -> AURAResponse:
        """Execute text generation through the gateway cascade with capability routing and cost tracking."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        # 1. Deterministic Candidate Selection (P2-M51-F02, P2-M51-F14, P2-M51-F15)
        pref = routing_preference or self.routing_strategy
        candidates = self.catalog.select_candidates(
            required_capabilities=required_capabilities,
            routing_preference=pref,
        )

        if not candidates:
            raise RuntimeError("ModelGateway has no registered providers.")

        metrics = get_metrics_registry()
        start_wall_time = time.time()
        attempted_chain: list[str] = []
        errors_diagnostic: list[dict[str, Any]] = []

        with self._tracer.start_span(
            "gateway.generate",
            kind=SpanKind.CLIENT,
            attributes={
                "gateway.request_id": str(request_id),
                "gateway.total_candidates": len(candidates),
                "gateway.fallback_enabled": self.fallback_enabled,
                "gateway.routing_preference": pref,
            },
        ) as root_span:
            max_attempts = min(len(candidates), self.max_fallback_attempts if self.fallback_enabled else 1)

            for attempt_idx in range(len(candidates)):
                reg = candidates[attempt_idx]
                provider_id = reg.provider_id
                attempted_chain.append(provider_id)
                tier_label = f"tier_{attempt_idx}"

                # 2. Check Circuit Breaker (P2-M51-F04, P2-M51-F25)
                if not reg.circuit_breaker.can_execute():
                    logger.warning(
                        "Gateway candidate '%s' circuit is %s. Skipping to next candidate.",
                        provider_id,
                        reg.circuit_breaker.state.value,
                    )
                    try:
                        metrics.get_counter("aura_gateway_requests_total").inc(
                            labels={"provider": provider_id, "status": "circuit_open", "fallback_tier": tier_label}
                        )
                        if attempt_idx + 1 < len(candidates):
                            next_prov = candidates[attempt_idx + 1].provider_id
                            metrics.get_counter("aura_gateway_fallbacks_total").inc(
                                labels={"from_provider": provider_id, "to_provider": next_prov, "reason": "circuit_open"}
                            )
                    except Exception:
                        pass
                    root_span.add_event(f"circuit_open_skipped:{provider_id}")
                    errors_diagnostic.append({
                        "provider": provider_id,
                        "error": f"Circuit breaker is {reg.circuit_breaker.state.value}",
                        "classification": FailureClassification.CIRCUIT_OPEN.value,
                    })
                    continue

                # 3. Attempt Provider Call
                provider_start_time = time.time()
                with self._tracer.start_span(
                    f"gateway.attempt:{provider_id}",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "provider.id": provider_id,
                        "provider.model": reg.model_name,
                        "provider.tier": attempt_idx,
                    },
                ) as attempt_span:
                    try:
                        logger.debug(
                            "Routing request %s to provider '%s' (model: %s, tier: %d, pricing: %s)",
                            request_id,
                            provider_id,
                            reg.model_name,
                            attempt_idx,
                            reg.cost_metadata.pricing_mode.value,
                        )

                        response = reg.provider.generate(prompt=prompt, request_id=request_id)
                        call_duration = time.time() - provider_start_time

                        # Success path (P2-M51-F18, P2-M51-F19, P2-M51-F20, P2-M51-F21)
                        reg.circuit_breaker.record_success()
                        attempt_span.set_status(SpanStatus.OK)
                        root_span.set_status(SpanStatus.OK)

                        # Extract token usage
                        prompt_tokens = 0
                        completion_tokens = 0
                        total_tokens = 0
                        meta = dict(response.metadata or {})
                        usage = meta.get("usage")
                        if isinstance(usage, dict):
                            prompt_tokens = int(usage.get("prompt_tokens") or 0)
                            completion_tokens = int(usage.get("completion_tokens") or 0)
                            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

                        # Cost computation from CostMetadata
                        computed_cost = reg.cost_metadata.compute_cost(prompt_tokens, completion_tokens)

                        try:
                            metrics.get_counter("aura_gateway_requests_total").inc(
                                labels={"provider": provider_id, "status": "success", "fallback_tier": tier_label}
                            )
                            metrics.get_histogram("aura_gateway_duration_seconds").observe(
                                call_duration, labels={"provider": provider_id, "status": "success"}
                            )
                        except Exception:
                            pass

                        # Enrich response metadata with provenance and cost
                        meta["gateway_provider"] = provider_id
                        meta["gateway_model"] = reg.model_name or meta.get("model", "")
                        meta["gateway_attempt_count"] = attempt_idx + 1
                        meta["gateway_fallback_occurred"] = attempt_idx > 0
                        meta["gateway_attempted_chain"] = attempted_chain
                        meta["gateway_total_latency_seconds"] = round(time.time() - start_wall_time, 4)
                        meta["gateway_pricing_mode"] = reg.cost_metadata.pricing_mode.value
                        meta["gateway_cost_usd"] = computed_cost
                        meta["gateway_context_window"] = reg.context_window
                        meta["gateway_capabilities"] = sorted(list(reg.capabilities))
                        meta["quota_state"] = "unknown"

                        response.metadata = scrub_dict(meta)
                        return response

                    except Exception as exc:
                        call_duration = time.time() - provider_start_time
                        classification = self.classifier.classify(exc)
                        sanitized_err = sanitize_error_message(str(exc))

                        attempt_span.record_exception(exc)
                        logger.warning(
                            "Provider '%s' failed on request %s (classification: %s, duration: %.3fs): %s",
                            provider_id,
                            request_id,
                            classification.value,
                            call_duration,
                            sanitized_err,
                        )

                        # Record failure on circuit breaker for transient errors
                        if classification in (
                            FailureClassification.TRANSIENT,
                            FailureClassification.RATE_LIMITED,
                            FailureClassification.QUOTA_EXHAUSTED,
                            FailureClassification.TIMEOUT,
                        ):
                            reg.circuit_breaker.record_failure(error=sanitized_err)

                        # Check fallback eligibility (P2-M51-F05, P2-M51-F06, P2-M51-F07, P2-M51-F08)
                        is_eligible = self.fallback_enabled and self.classifier.is_fallback_eligible(classification)
                        can_try_next = is_eligible and (len(attempted_chain) < max_attempts) and (attempt_idx + 1 < len(candidates))

                        if not is_eligible:
                            # Strict Fail-Closed invariant for FATAL, POLICY_DENY, AUTH_FAILURE, CAPABILITY_MISMATCH
                            try:
                                metrics.get_counter("aura_gateway_requests_total").inc(
                                    labels={"provider": provider_id, "status": classification.value, "fallback_tier": tier_label}
                                )
                                metrics.get_histogram("aura_gateway_duration_seconds").observe(
                                    call_duration, labels={"provider": provider_id, "status": classification.value}
                                )
                            except Exception:
                                pass
                            root_span.record_exception(exc)
                            # Re-raise original error immediately without fallback
                            raise

                        # Transient error eligible for fallback (P2-M51-F05, P2-M51-F16)
                        try:
                            metrics.get_counter("aura_gateway_requests_total").inc(
                                labels={"provider": provider_id, "status": "fallback", "fallback_tier": tier_label}
                            )
                            metrics.get_histogram("aura_gateway_duration_seconds").observe(
                                call_duration, labels={"provider": provider_id, "status": "fallback"}
                            )
                            if can_try_next:
                                next_provider_id = candidates[attempt_idx + 1].provider_id
                                metrics.get_counter("aura_gateway_fallbacks_total").inc(
                                    labels={
                                        "from_provider": provider_id,
                                        "to_provider": next_provider_id,
                                        "reason": classification.value,
                                    }
                                )
                        except Exception:
                            pass

                        root_span.add_event(
                            f"fallback_transition:{provider_id}->{classification.value}",
                            attributes={"error": sanitized_err},
                        )

                        errors_diagnostic.append({
                            "provider": provider_id,
                            "error": sanitized_err,
                            "classification": classification.value,
                        })

                        if not can_try_next:
                            break

            # If we reached here, all attempts exhausted (P2-M51-F29)
            total_duration = time.time() - start_wall_time
            root_span.set_status(SpanStatus.ERROR)
            diag_str = "; ".join(f"[{e['provider']}: {e['classification']} - {e['error']}]" for e in errors_diagnostic)
            err_msg = (
                f"ModelGateway failed: All configured providers ({', '.join(attempted_chain)}) "
                f"failed or were unavailable after {len(attempted_chain)} attempts in {total_duration:.3f}s. "
                f"Diagnostic details: {diag_str}"
            )
            raise RuntimeError(err_msg)
