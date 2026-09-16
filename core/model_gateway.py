"""M51 Multi-Provider Model Gateway & Intelligent Provider Routing.

Provides a unified ModelInterface-compliant entry point for routing requests
across multiple LLM providers (Gemini, Groq, OpenAI, Generic/Local, Fake)
with priority cascading, circuit-breaker isolation, transient failure recovery,
fail-closed policy enforcement, and low-cardinality telemetry.
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


class FailureClassification(str, Enum):
    """Categorization of model invocation failures."""

    SUCCESS = "success"
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
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
    )

    # Rate limiting & Quota exhaustion (eligible for fallback)
    RATE_LIMIT_PATTERNS = (
        "429",
        "ratelimiterror",
        "rate limit",
        "quota exceeded",
        "resourceexhausted",
        "too many requests",
        "exceeded your current quota",
        "credits expired",
    )

    # Network / Timeout / Transient 5xx server errors (eligible for fallback)
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
    )

    @classmethod
    def classify(cls, exc: Exception | str) -> FailureClassification:
        """Classify an exception or error string into a FailureClassification."""
        err_msg = str(exc).lower() if exc is not None else ""
        exc_type_name = type(exc).__name__.lower() if isinstance(exc, Exception) else ""

        # 1. Policy / Security violations (Strict Priority 1: Fail Closed)
        if any(p in err_msg for p in cls.POLICY_DENY_PATTERNS) or "policy" in exc_type_name or "security" in exc_type_name:
            return FailureClassification.POLICY_DENY

        # 2. Auth / Permission failures (Priority 2: Fail Closed)
        if any(p in err_msg for p in cls.AUTH_FAILURE_PATTERNS) or "authentication" in exc_type_name:
            return FailureClassification.AUTH_FAILURE

        # 3. Bad request / Schema / Invalid input errors (Fatal)
        if "bad request" in err_msg or "badrequesterror" in exc_type_name or "invalid url scheme" in err_msg or "missing hostname" in err_msg:
            return FailureClassification.FATAL

        # 4. Rate limiting / Quota (Transient)
        if any(p in err_msg for p in cls.RATE_LIMIT_PATTERNS) or "ratelimit" in exc_type_name:
            return FailureClassification.RATE_LIMITED

        # 5. Timeouts (Transient)
        if any(p in err_msg for p in ("timeout", "timed out", "timeouterror")) or "timeout" in exc_type_name:
            return FailureClassification.TIMEOUT

        # 6. Transient 5xx / Connection errors (Transient)
        if any(p in err_msg for p in cls.TRANSIENT_PATTERNS) or "connection" in exc_type_name:
            return FailureClassification.TRANSIENT

        # Default: If unexpected RuntimeError from provider wrapped call, check inner details
        if "generic model provider generation failed" in err_msg:
            return FailureClassification.TRANSIENT

        return FailureClassification.FATAL

    @classmethod
    def is_fallback_eligible(cls, classification: FailureClassification) -> bool:
        """Determine if a failure classification is eligible for fallback cascade."""
        return classification in (
            FailureClassification.TRANSIENT,
            FailureClassification.RATE_LIMITED,
            FailureClassification.TIMEOUT,
            FailureClassification.CIRCUIT_OPEN,
        )


@dataclass
class ProviderRegistration:
    """Descriptor and runtime handle for a registered model provider in the gateway."""

    provider_id: str
    provider: ModelInterface
    priority: int = 100
    weight: float = 1.0
    circuit_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker())
    timeout_seconds: float = 30.0
    is_fallback: bool = False
    model_name: str = ""

    def __post_init__(self):
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be a non-empty string.")
        self.provider_id = self.provider_id.strip().lower()
        if not isinstance(self.provider, ModelInterface):
            raise TypeError("provider must implement ModelInterface.")
        self.priority = int(self.priority)
        self.weight = float(self.weight)
        self.timeout_seconds = float(self.timeout_seconds)
        if not self.model_name and hasattr(self.provider, "model_name"):
            self.model_name = str(getattr(self.provider, "model_name", ""))

    def to_dict(self) -> dict[str, Any]:
        """Return serialized metadata snapshot without secrets."""
        return {
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "priority": self.priority,
            "weight": self.weight,
            "timeout_seconds": self.timeout_seconds,
            "is_fallback": self.is_fallback,
            "circuit_breaker": self.circuit_breaker.get_status_dict(),
        }


class ProviderCatalog:
    """Thread-safe catalog of registered model providers."""

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

    def get_primary(self) -> ProviderRegistration | None:
        """Get the highest-priority primary (non-fallback) provider."""
        with self._lock:
            candidates = [r for r in self.list_providers() if not r.is_fallback]
            if candidates:
                return candidates[0]
            all_provs = self.list_providers()
            return all_provs[0] if all_provs else None

    def get_fallbacks(self) -> list[ProviderRegistration]:
        """Get ordered list of fallback providers."""
        with self._lock:
            primary = self.get_primary()
            primary_id = primary.provider_id if primary else None
            return [r for r in self.list_providers() if r.provider_id != primary_id]

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
    ) -> AURAResponse:
        """Execute text generation through the gateway cascade."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        candidates = self.catalog.list_providers()
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
            },
        ) as root_span:
            max_attempts = min(len(candidates), self.max_fallback_attempts if self.fallback_enabled else 1)

            for attempt_idx in range(len(candidates)):
                reg = candidates[attempt_idx]
                provider_id = reg.provider_id
                attempted_chain.append(provider_id)
                tier_label = f"tier_{attempt_idx}"

                # 1. Check Circuit Breaker
                if not reg.circuit_breaker.can_execute():
                    logger.warning(
                        "Gateway candidate '%s' circuit is %s. Skipping to next candidate.",
                        provider_id,
                        reg.circuit_breaker.state.value,
                    )
                    metrics.get_counter("aura_gateway_requests_total").inc(
                        labels={"provider": provider_id, "status": "circuit_open", "fallback_tier": tier_label}
                    )
                    if attempt_idx + 1 < len(candidates):
                        next_prov = candidates[attempt_idx + 1].provider_id
                        metrics.get_counter("aura_gateway_fallbacks_total").inc(
                            labels={"from_provider": provider_id, "to_provider": next_prov, "reason": "circuit_open"}
                        )
                    root_span.add_event(f"circuit_open_skipped:{provider_id}")
                    errors_diagnostic.append({
                        "provider": provider_id,
                        "error": f"Circuit breaker is {reg.circuit_breaker.state.value}",
                        "classification": FailureClassification.CIRCUIT_OPEN.value,
                    })
                    continue

                # 2. Attempt Provider Call
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
                            "Routing request %s to provider '%s' (model: %s, tier: %d)",
                            request_id,
                            provider_id,
                            reg.model_name,
                            attempt_idx,
                        )

                        response = reg.provider.generate(prompt=prompt, request_id=request_id)
                        call_duration = time.time() - provider_start_time

                        # Success path
                        reg.circuit_breaker.record_success()
                        attempt_span.set_status(SpanStatus.OK)
                        root_span.set_status(SpanStatus.OK)

                        try:
                            metrics.get_counter("aura_gateway_requests_total").inc(
                                labels={"provider": provider_id, "status": "success", "fallback_tier": tier_label}
                            )
                            metrics.get_histogram("aura_gateway_duration_seconds").observe(
                                call_duration, labels={"provider": provider_id, "status": "success"}
                            )
                        except Exception:
                            pass

                        # Enrich metadata
                        meta = dict(response.metadata or {})
                        meta["gateway_provider"] = provider_id
                        meta["gateway_model"] = reg.model_name or meta.get("model", "")
                        meta["gateway_attempt_count"] = attempt_idx + 1
                        meta["gateway_fallback_occurred"] = attempt_idx > 0
                        meta["gateway_attempted_chain"] = attempted_chain
                        meta["gateway_total_latency_seconds"] = round(time.time() - start_wall_time, 4)

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
                            FailureClassification.TIMEOUT,
                        ):
                            reg.circuit_breaker.record_failure(error=sanitized_err)

                        # Check fallback eligibility
                        is_eligible = self.fallback_enabled and self.classifier.is_fallback_eligible(classification)
                        can_try_next = is_eligible and (len(attempted_chain) < max_attempts) and (attempt_idx + 1 < len(candidates))

                        if not is_eligible:
                            # Strict Fail-Closed invariant for FATAL, POLICY_DENY, AUTH_FAILURE, or disabled fallback
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

                        # Transient error eligible for fallback
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

            # If we reached here, all attempts exhausted
            total_duration = time.time() - start_wall_time
            root_span.set_status(SpanStatus.ERROR)
            diag_str = "; ".join(f"[{e['provider']}: {e['classification']} - {e['error']}]" for e in errors_diagnostic)
            err_msg = (
                f"ModelGateway failed: All configured providers ({', '.join(attempted_chain)}) "
                f"failed or were unavailable after {len(attempted_chain)} attempts in {total_duration:.3f}s. "
                f"Diagnostic details: {diag_str}"
            )
            raise RuntimeError(err_msg)
