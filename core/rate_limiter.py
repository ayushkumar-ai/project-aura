"""M60 — Production Public API Rate Limiter for Project AURA.

Implements a deterministic, thread-safe, bounded-memory token bucket rate limiter:
- Category-scoped rate limiting (auth, general_api, agent_execution, multimodal, admin)
- Authenticated primary identity: (tenant_id, user_id, operation_category)
- Unauthenticated identity: (client_ip, operation_category)
- Monotonic interval time tracking (immune to NTP/wall-clock steps)
- Standard rate limit response metadata (Limit, Remaining, Reset, Retry-After)
- LRU bounded capacity eviction (max_buckets cap prevents memory exhaustion)
- Stale bucket cleanup maintenance
- Low-cardinality Prometheus telemetry (category, status)
"""

from __future__ import annotations

import enum
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Sequence

from core.metrics import get_metrics_registry

logger = logging.getLogger("aura.rate_limiter")


class OperationCategory(str, enum.Enum):
    """Bounded rate-limit operation categories."""
    AUTH = "auth"
    GENERAL_API = "general_api"
    AGENT_EXECUTION = "agent_execution"
    MULTIMODAL = "multimodal"
    ADMIN = "admin"


# Default limits per category: (requests_per_minute, burst_capacity)
DEFAULT_CATEGORY_LIMITS: dict[OperationCategory, tuple[int, int]] = {
    OperationCategory.AUTH: (10, 5),
    OperationCategory.GENERAL_API: (60, 10),
    OperationCategory.AGENT_EXECUTION: (20, 5),
    OperationCategory.MULTIMODAL: (30, 5),
    OperationCategory.ADMIN: (30, 5),
}


def map_path_to_category(path: str) -> OperationCategory | None:
    """Map an incoming HTTP request path to a bounded OperationCategory.
    
    Returns None for unthrottled endpoints (e.g. /health).
    """
    normalized = path.strip().split("?")[0].rstrip("/")
    if not normalized:
        normalized = "/"

    # Health and liveness probes are unthrottled
    if normalized in ("/health", "/healthz", "/live", "/livez"):
        return None

    # Authentication & token operations
    if normalized.startswith(("/api/v1/auth", "/v1/auth")):
        return OperationCategory.AUTH

    # Agent execution, planning, mesh dispatch
    if normalized.startswith(
        (
            "/api/v1/agents/run",
            "/api/v1/agent-mesh/dispatch",
            "/api/v1/agent-runs",
            "/v1/run",
            "/v1/task",
            "/v1/plan",
            "/v1/cycle",
        )
    ):
        return OperationCategory.AGENT_EXECUTION

    # Multimodal processing and uploads
    if normalized.startswith(("/api/v1/multimodal", "/v1/multimodal")):
        return OperationCategory.MULTIMODAL

    # Administration, telemetry, approvals
    if normalized.startswith(
        (
            "/api/v1/admin",
            "/v1/telemetry",
            "/api/v1/approvals",
            "/v1/approvals",
        )
    ):
        return OperationCategory.ADMIN

    # General API endpoints (workspaces, devices, memories, webhooks, tasks listing, etc.)
    return OperationCategory.GENERAL_API


@dataclass
class TokenBucket:
    """Thread-safe single token bucket state."""

    capacity: float
    refill_rate: float  # tokens per second (requests_per_minute / 60.0)
    tokens: float
    last_refill: float  # monotonic seconds
    last_accessed: float  # monotonic seconds

    def refill_and_consume(self, cost: float, now_mono: float) -> tuple[bool, float, float]:
        """Refill bucket based on elapsed monotonic time and attempt to consume cost tokens.
        
        Returns (allowed, current_tokens, capacity).
        """
        elapsed = max(0.0, now_mono - self.last_refill)
        # Refill tokens up to burst capacity (unused tokens never accumulate beyond capacity)
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))
        self.last_refill = now_mono
        self.last_accessed = now_mono

        if self.tokens >= cost:
            self.tokens -= cost
            return True, self.tokens, self.capacity
        else:
            return False, self.tokens, self.capacity


@dataclass(frozen=True)
class RateLimitResult:
    """Result of a rate limit check with standard response metadata."""

    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int  # Wall-clock Unix timestamp (seconds)
    retry_after: int  # Seconds until next token is available (0 if allowed)
    category: str


class RateLimiter:
    """Deterministic, thread-safe in-process token bucket rate limiter with bounded LRU memory."""

    def __init__(
        self,
        max_buckets: int = 50000,
        category_limits: dict[OperationCategory, tuple[int, int]] | None = None,
        cleanup_interval_seconds: float = 3600.0,
    ) -> None:
        self.max_buckets = max_buckets
        self.limits: dict[OperationCategory, tuple[int, int]] = dict(
            category_limits or DEFAULT_CATEGORY_LIMITS
        )
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()
        self._last_cleanup_mono = time.monotonic()
        self._init_telemetry()

    def _init_telemetry(self) -> None:
        """Register low-cardinality rate limiting metrics."""
        try:
            reg = get_metrics_registry()
            reg.register_counter(
                "aura_rate_limit_requests_total",
                "Total HTTP rate limiter evaluations by category and outcome status",
                allowed_labels=["category", "status"],
            )
        except Exception:
            pass

    def check_rate_limit(
        self,
        identity_key: str,
        category: OperationCategory | str,
        cost: float = 1.0,
    ) -> RateLimitResult:
        """Check rate limit for given identity key and operation category.
        
        identity_key: Authenticated tuple (tenant_id:user_id) or sanitized client IP.
        category: OperationCategory.
        """
        if isinstance(category, str):
            try:
                cat = OperationCategory(category)
            except ValueError:
                cat = OperationCategory.GENERAL_API
        else:
            cat = category

        rpm, burst = self.limits.get(cat, DEFAULT_CATEGORY_LIMITS[OperationCategory.GENERAL_API])
        refill_rate = float(rpm) / 60.0

        now_mono = time.monotonic()
        now_wall = time.time()
        bucket_key = f"{cat.value}:{identity_key}"

        with self._lock:
            # Check if background cleanup is due
            if now_mono - self._last_cleanup_mono >= self.cleanup_interval_seconds:
                self._cleanup_stale_locked(now_mono, max_idle_seconds=self.cleanup_interval_seconds)
                self._last_cleanup_mono = now_mono

            bucket = self._buckets.get(bucket_key)
            if bucket is not None:
                # LRU: move existing bucket to most recently used end
                self._buckets.move_to_end(bucket_key)
            else:
                # Evict oldest entry if at maximum capacity limit
                if len(self._buckets) >= self.max_buckets:
                    self._buckets.popitem(last=False)

                bucket = TokenBucket(
                    capacity=float(burst),
                    refill_rate=refill_rate,
                    tokens=float(burst),
                    last_refill=now_mono,
                    last_accessed=now_mono,
                )
                self._buckets[bucket_key] = bucket

            allowed, current_tokens, cap = bucket.refill_and_consume(cost, now_mono)

            # Calculate reset epoch (when bucket will reach full burst capacity)
            needed_for_full = max(0.0, cap - current_tokens)
            seconds_to_full = (needed_for_full / refill_rate) if refill_rate > 0 else 0.0
            reset_epoch = int(math.ceil(now_wall + seconds_to_full))

            # Calculate retry-after on 429
            if allowed:
                retry_after = 0
            else:
                needed_for_one = max(0.0, cost - current_tokens)
                retry_after = int(math.ceil(needed_for_one / refill_rate)) if refill_rate > 0 else 60

            remaining = max(0, int(math.floor(current_tokens)))

        # Record low-cardinality telemetry
        self._record_metric(cat.value, "allowed" if allowed else "throttled")

        return RateLimitResult(
            allowed=allowed,
            limit=burst,
            remaining=remaining,
            reset_epoch=reset_epoch,
            retry_after=retry_after,
            category=cat.value,
        )

    def _record_metric(self, category: str, status: str) -> None:
        """Safely record low-cardinality rate limit metric."""
        try:
            reg = get_metrics_registry()
            counter = reg.get_counter("aura_rate_limit_requests_total")
            counter.inc(labels={"category": category, "status": status})
        except Exception:
            pass

    def _cleanup_stale_locked(self, now_mono: float, max_idle_seconds: float) -> int:
        """Internal helper to remove stale idle buckets under lock."""
        keys_to_remove: list[str] = []
        for key, bucket in self._buckets.items():
            if now_mono - bucket.last_accessed > max_idle_seconds:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._buckets[key]
        return len(keys_to_remove)

    def cleanup_stale_buckets(self, max_idle_seconds: float = 3600.0) -> int:
        """Public method to purge inactive buckets older than max_idle_seconds."""
        now_mono = time.monotonic()
        with self._lock:
            removed = self._cleanup_stale_locked(now_mono, max_idle_seconds)
            self._last_cleanup_mono = now_mono
            return removed

    def get_bucket_count(self) -> int:
        """Return current number of active tracked buckets."""
        with self._lock:
            return len(self._buckets)

    def clear(self) -> None:
        """Clear all active buckets (used for isolated test teardown)."""
        with self._lock:
            self._buckets.clear()
            self._last_cleanup_mono = time.monotonic()


# Global singleton instance
_global_rate_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def get_rate_limiter(config: Any | None = None) -> RateLimiter:
    """Get or create the global singleton RateLimiter instance."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        with _limiter_lock:
            if _global_rate_limiter is None:
                max_b = getattr(config, "aura_rate_limit_max_buckets", 50000) if config else 50000
                cat_limits = dict(DEFAULT_CATEGORY_LIMITS)
                if config:
                    cat_limits[OperationCategory.AUTH] = (
                        getattr(config, "aura_rate_limit_auth_rpm", 10),
                        getattr(config, "aura_rate_limit_auth_burst", 5),
                    )
                    cat_limits[OperationCategory.AGENT_EXECUTION] = (
                        getattr(config, "aura_rate_limit_agent_execution_rpm", 20),
                        getattr(config, "aura_rate_limit_agent_execution_burst", 5),
                    )
                    cat_limits[OperationCategory.MULTIMODAL] = (
                        getattr(config, "aura_rate_limit_multimodal_rpm", 30),
                        getattr(config, "aura_rate_limit_multimodal_burst", 5),
                    )
                    cat_limits[OperationCategory.ADMIN] = (
                        getattr(config, "aura_rate_limit_admin_rpm", 30),
                        getattr(config, "aura_rate_limit_admin_burst", 5),
                    )
                    cat_limits[OperationCategory.GENERAL_API] = (
                        getattr(config, "aura_rate_limit_requests_per_minute", 60),
                        getattr(config, "aura_rate_limit_burst_size", 10),
                    )
                _global_rate_limiter = RateLimiter(max_buckets=max_b, category_limits=cat_limits)
    return _global_rate_limiter


def reset_rate_limiter() -> None:
    """Reset the global rate limiter (for test isolation)."""
    global _global_rate_limiter
    with _limiter_lock:
        if _global_rate_limiter is not None:
            _global_rate_limiter.clear()
        _global_rate_limiter = None
