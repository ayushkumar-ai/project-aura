import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouteResult, ModelRouter, TaskRequirements
from core.models import AURAResponse
from core.provider_health import ProviderHealthTracker, ProviderHealthStatus
from core.provider_registry import ProviderRegistry
from core.resource_budget import ResourceBudgetManager
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.resilient_router")


class FallbackStrategy(str, Enum):
    """Routing and fallback ordering strategy."""

    CAPABILITY_FIRST = "capability_first"
    LATENCY_FIRST = "latency_first"
    ORDERED = "ordered"


class RoutingOutcome(str, Enum):
    """Outcome report classification for a routed model execution."""

    SUCCESS_PRIMARY = "success_primary"
    SUCCESS_FALLBACK = "success_fallback"
    EXHAUSTED = "exhausted"
    CIRCUIT_TRIPPED = "circuit_tripped"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class RoutingTelemetry:
    """Operational telemetry capturing provider selection, fallbacks, latencies, and circuit states."""

    request_id: str
    selected_primary_provider_id: str
    attempted_provider_ids: list[str] = field(default_factory=list)
    successful_provider_id: str | None = None
    fallback_count: int = 0
    circuit_states: dict[str, str] = field(default_factory=dict)
    total_latency_seconds: float = 0.0
    outcome: RoutingOutcome = RoutingOutcome.SUCCESS_PRIMARY
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert telemetry to a serializable dictionary without secrets."""
        return {
            "request_id": self.request_id,
            "selected_primary_provider_id": self.selected_primary_provider_id,
            "attempted_provider_ids": list(self.attempted_provider_ids),
            "successful_provider_id": self.successful_provider_id,
            "fallback_count": self.fallback_count,
            "circuit_states": dict(self.circuit_states),
            "total_latency_seconds": round(self.total_latency_seconds, 4),
            "outcome": self.outcome.value,
            "error": self.error,
        }


class ModelRoutingExhaustedError(RuntimeError):
    """Raised when all primary and fallback model candidates fail or are unavailable."""
    pass


class ResilientModelRouter(ModelRouter):
    """Resilient model router with health-aware selection, circuit breaker evaluation, and bounded fallback cascades."""

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        provider_registry: ProviderRegistry,
        health_tracker: ProviderHealthTracker | None = None,
        max_fallback_attempts: int | None = None,
        fallback_enabled: bool | None = None,
        fallback_strategy: FallbackStrategy | str = FallbackStrategy.CAPABILITY_FIRST,
        budget_manager: ResourceBudgetManager | None = None,
    ):
        super().__init__(
            capability_registry=capability_registry,
            provider_registry=provider_registry,
        )
        self.health_tracker = health_tracker if health_tracker is not None else ProviderHealthTracker()
        self.max_fallback_attempts: int = (
            int(max_fallback_attempts)
            if max_fallback_attempts is not None
            else int(getattr(settings, "aura_max_model_fallback_attempts", 2))
        )
        self.fallback_enabled: bool = (
            bool(fallback_enabled)
            if fallback_enabled is not None
            else bool(getattr(settings, "aura_model_fallback_enabled", True))
        )
        self.fallback_strategy = (
            FallbackStrategy(fallback_strategy)
            if isinstance(fallback_strategy, str)
            else fallback_strategy
        )
        self.budget_manager = budget_manager
        self._lock = threading.RLock()

    def route(
        self,
        requirements: TaskRequirements | None = None,
        exclude_provider_ids: set[str] | Sequence[str] | None = None,
        allow_unhealthy_canary: bool = True,
    ) -> ModelRouteResult:
        """Select a compatible model and provider, avoiding excluded or tripped-circuit endpoints."""
        reqs = requirements if requirements is not None else TaskRequirements()
        excluded = {str(p).strip().lower() for p in exclude_provider_ids} if exclude_provider_ids else set()

        with self._lock:
            # 1. Handle explicitly preferred model if requested and not excluded
            if reqs.preferred_model is not None:
                model_id = reqs.preferred_model
                if self.capability_registry.has_model(model_id):
                    descriptor = self.capability_registry.get_model(model_id)
                    norm_pid = descriptor.provider_id.strip().lower()

                    if norm_pid not in excluded and self.provider_registry.has(norm_pid):
                        # Verify capabilities
                        if all(descriptor.supports(cap) for cap in reqs.required_capabilities):
                            if reqs.preferred_provider is None or norm_pid == reqs.preferred_provider.strip().lower():
                                # Check health & circuit breaker
                                if self.health_tracker.is_provider_healthy(norm_pid) or allow_unhealthy_canary:
                                    provider = self.provider_registry.get(norm_pid)
                                    return ModelRouteResult(model_descriptor=descriptor, provider=provider)

            # 2. Check if preferred provider has a valid candidate (not excluded and healthy)
            if reqs.preferred_provider is not None:
                norm_pref_pid = reqs.preferred_provider.strip().lower()
                if norm_pref_pid not in excluded and self.provider_registry.has(norm_pref_pid):
                    pref_candidates = [
                        m for m in self.capability_registry.list_models(provider_id=norm_pref_pid)
                        if all(m.supports(cap) for cap in reqs.required_capabilities)
                    ]
                    if pref_candidates:
                        healthy_pref = [
                            m for m in pref_candidates
                            if self.health_tracker.is_provider_healthy(norm_pref_pid)
                        ]
                        if healthy_pref:
                            chosen = healthy_pref[0]
                            provider = self.provider_registry.get(norm_pref_pid)
                            return ModelRouteResult(model_descriptor=chosen, provider=provider)
                        elif allow_unhealthy_canary and not any(
                            self.health_tracker.is_provider_healthy(m.provider_id.strip().lower())
                            for m in self.capability_registry.list_models()
                            if m.provider_id.strip().lower() not in excluded and self.provider_registry.has(m.provider_id.strip().lower())
                        ):
                            # Only canary if no healthy alternatives exist across registry
                            chosen = pref_candidates[0]
                            provider = self.provider_registry.get(norm_pref_pid)
                            return ModelRouteResult(model_descriptor=chosen, provider=provider)

            # 3. Query all candidates from CapabilityRegistry for fallback or unconstrained routing
            candidates = self.capability_registry.list_models()

            # 4. Filter candidates by capability, exclusion, and provider registry presence
            valid_candidates: list[ModelDescriptor] = []
            for m in candidates:
                norm_pid = m.provider_id.strip().lower()
                if norm_pid in excluded:
                    continue
                if not self.provider_registry.has(norm_pid):
                    continue
                if not all(m.supports(cap) for cap in reqs.required_capabilities):
                    continue
                valid_candidates.append(m)

            if not valid_candidates:
                raise ValueError(
                    f"No compatible model found matching required capabilities {sorted(reqs.required_capabilities)} "
                    f"(excluded: {sorted(excluded)})."
                )

            # 5. Partition by health & circuit breaker state
            healthy_candidates = [
                m for m in valid_candidates
                if self.health_tracker.is_provider_healthy(m.provider_id.strip().lower())
            ]

            effective_candidates = healthy_candidates if healthy_candidates else valid_candidates

            # 6. Order candidates by FallbackStrategy
            if self.fallback_strategy == FallbackStrategy.LATENCY_FIRST:
                selected_model = sorted(
                    effective_candidates,
                    key=lambda m: (
                        self.health_tracker.get_metrics(m.provider_id.strip().lower()).moving_average_latency_seconds,
                        m.provider_id.strip().lower(),
                        m.model_id.strip().lower(),
                    ),
                )[0]
            else:
                selected_model = sorted(
                    effective_candidates,
                    key=lambda m: (m.provider_id.strip().lower(), m.model_id.strip().lower()),
                )[0]

            provider = self.provider_registry.get(selected_model.provider_id.strip().lower())
            return ModelRouteResult(model_descriptor=selected_model, provider=provider)

    def generate_with_fallback(
        self,
        prompt: str,
        request_id: UUID | None = None,
        requirements: TaskRequirements | None = None,
        timeout: float | None = None,
    ) -> tuple[AURAResponse, RoutingTelemetry]:
        """Execute model generation with automatic health-aware fallback and loop prevention."""
        actual_req_id = request_id if request_id is not None else uuid4()
        reqs = requirements if requirements is not None else TaskRequirements()
        max_attempts = (self.max_fallback_attempts + 1) if self.fallback_enabled else 1

        visited_providers: set[str] = set()
        attempted_providers: list[str] = []
        circuit_states: dict[str, str] = {}
        total_latency = 0.0
        primary_provider_id: str = ""
        last_exception: Exception | None = None

        for attempt in range(max_attempts):
            try:
                route_res = self.route(
                    requirements=reqs,
                    exclude_provider_ids=visited_providers,
                )
            except Exception as route_err:
                logger.warning(
                    "Routing resolution failed on attempt %d for request '%s': %s",
                    attempt + 1,
                    actual_req_id,
                    route_err,
                )
                last_exception = route_err
                break

            provider_id = route_res.provider_id.strip().lower()
            if not primary_provider_id:
                primary_provider_id = provider_id

            visited_providers.add(provider_id)
            attempted_providers.append(provider_id)

            cb = self.health_tracker.get_or_create_circuit_breaker(provider_id)
            circuit_states[provider_id] = cb.state.value

            start_t = time.time()
            try:
                # Attempt model generation
                response = route_res.provider.generate(prompt=prompt, request_id=actual_req_id)
                elapsed = time.time() - start_t
                total_latency += elapsed

                if response is None or not hasattr(response, "content"):
                    raise ValueError(f"Provider '{provider_id}' returned empty or invalid response.")

                self.health_tracker.record_call_success(provider_id, latency_seconds=elapsed)
                circuit_states[provider_id] = cb.state.value

                outcome = RoutingOutcome.SUCCESS_PRIMARY if len(attempted_providers) == 1 else RoutingOutcome.SUCCESS_FALLBACK
                telemetry = RoutingTelemetry(
                    request_id=str(actual_req_id),
                    selected_primary_provider_id=primary_provider_id,
                    attempted_provider_ids=attempted_providers,
                    successful_provider_id=provider_id,
                    fallback_count=len(attempted_providers) - 1,
                    circuit_states=circuit_states,
                    total_latency_seconds=total_latency,
                    outcome=outcome,
                )
                return response, telemetry

            except Exception as gen_err:
                elapsed = time.time() - start_t
                total_latency += elapsed
                logger.warning(
                    "Model call failed on provider '%s' (attempt %d/%d): %s",
                    provider_id,
                    attempt + 1,
                    max_attempts,
                    gen_err,
                )
                self.health_tracker.record_call_failure(provider_id, error=gen_err, latency_seconds=elapsed)
                circuit_states[provider_id] = cb.state.value
                last_exception = gen_err

                if not self.fallback_enabled:
                    break

        # If loop finishes without returning, all attempts failed
        telemetry = RoutingTelemetry(
            request_id=str(actual_req_id),
            selected_primary_provider_id=primary_provider_id or "none",
            attempted_provider_ids=attempted_providers,
            successful_provider_id=None,
            fallback_count=max(0, len(attempted_providers) - 1),
            circuit_states=circuit_states,
            total_latency_seconds=total_latency,
            outcome=RoutingOutcome.EXHAUSTED,
            error=str(last_exception),
        )
        raise ModelRoutingExhaustedError(
            f"All model provider candidates failed for request '{actual_req_id}'. "
            f"Attempted: {attempted_providers}. Last error: {last_exception}"
        ) from last_exception
