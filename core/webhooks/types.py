"""M54 — Enterprise Webhooks & Event Gateway Data Types and Models."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class WebhookEndpointStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class WebhookKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InboundEventStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class SubscriptionTargetType(str, enum.Enum):
    WEBHOOK = "webhook"
    TASK = "task"
    AUTOMATION = "automation"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class DeadLetterType(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


# Exception Hierarchy
class WebhookError(Exception):
    """Base exception for all webhook and event gateway operations."""
    pass


class SSRFViolationError(WebhookError):
    """Raised when an outbound URL resolves to a prohibited IP address."""
    pass


class SSRFMultiAddressViolationError(SSRFViolationError):
    """Raised when a hostname resolves to multiple IPs and at least one is prohibited."""
    pass


class InvalidWebhookPortError(WebhookError):
    """Raised when a webhook target URL specifies a port outside 443..8443."""
    pass


class EventLoopDetectedError(WebhookError):
    """Raised when causation depth > 3 or a cycle is detected in event lineage."""
    pass


class WebhookSignatureError(WebhookError):
    """Raised when cryptographic HMAC signature verification fails."""
    pass


class WebhookTimestampError(WebhookError):
    """Raised when webhook request timestamp is outside the +-300s window."""
    pass


class PayloadCollisionError(WebhookError):
    """Raised when a duplicate provider_event_id is submitted with an altered payload hash."""
    pass


class TenantCapacityExceededError(WebhookError):
    """Raised when tenant in-flight webhook capacity exceeds the 100 limit."""
    pass


class InvalidKeyStatusError(WebhookError):
    """Raised when an operation is attempted with an invalid key status."""
    pass


class KeyUnavailableError(WebhookError):
    """Raised when the master key or required signing key is unavailable."""
    pass


class StaleLeaseError(WebhookError):
    """Raised when a worker attempts to update a lease it no longer owns."""
    pass


class ImmutableStateError(WebhookError):
    """Raised when an attempt is made to mutate a terminal record."""
    pass


# Data Classes
@dataclass
class TenantWebhookCapacity:
    user_id: str
    in_flight_count: int = 0
    max_capacity: int = 100
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "in_flight_count": self.in_flight_count,
            "max_capacity": self.max_capacity,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at),
        }


@dataclass
class WebhookEndpoint:
    id: str
    user_id: str
    name: str
    path_suffix: str = ""
    description: str = ""
    status: WebhookEndpointStatus = WebhookEndpointStatus.ACTIVE
    allowed_event_types: list[str] = field(default_factory=lambda: ["*"])
    rate_limit_per_minute: int = 120
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "path_suffix": self.path_suffix,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, enum.Enum) else str(self.status),
            "allowed_event_types": self.allowed_event_types,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at),
        }


@dataclass
class WebhookSigningKey:
    id: str
    endpoint_id: str
    user_id: str
    encrypted_secret: dict[str, Any]
    key_version: int = 1
    key_status: WebhookKeyStatus = WebhookKeyStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.key_status == WebhookKeyStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "endpoint_id": self.endpoint_id,
            "user_id": self.user_id,
            "key_version": self.key_version,
            "key_status": self.key_status.value if isinstance(self.key_status, enum.Enum) else str(self.key_status),
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            # Plaintext secret is never included
            "secret_masked": "****",
        }


@dataclass
class InboundEvent:
    id: str
    endpoint_id: str
    user_id: str
    provider_event_id: str
    payload_sha256: str
    event_type: str
    payload: dict[str, Any]
    signature: str
    status: InboundEventStatus = InboundEventStatus.ACCEPTED
    attempts: int = 0
    max_attempts: int = 3
    next_attempt_at: datetime = field(default_factory=datetime.utcnow)
    last_attempt_at: datetime | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    lease_owner: str | None = None
    lease_token: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    task_id: str | None = None
    error_message: str | None = None
    received_at: datetime = field(default_factory=datetime.utcnow)
    processed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "endpoint_id": self.endpoint_id,
            "user_id": self.user_id,
            "provider_event_id": self.provider_event_id,
            "payload_sha256": self.payload_sha256,
            "event_type": self.event_type,
            "status": self.status.value if isinstance(self.status, enum.Enum) else str(self.status),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_attempt_at": self.next_attempt_at.isoformat() if isinstance(self.next_attempt_at, datetime) else str(self.next_attempt_at),
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "task_id": self.task_id,
            "error_message": self.error_message,
            "received_at": self.received_at.isoformat() if isinstance(self.received_at, datetime) else str(self.received_at),
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }


@dataclass
class EventSubscription:
    id: str
    user_id: str
    name: str
    event_type_filter: str
    target_type: SubscriptionTargetType
    target_url: str | None = None
    signing_secret_encrypted: dict[str, Any] | None = None
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "event_type_filter": self.event_type_filter,
            "target_type": self.target_type.value if isinstance(self.target_type, enum.Enum) else str(self.target_type),
            "target_url": self.target_url,
            "status": self.status.value if isinstance(self.status, enum.Enum) else str(self.status),
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at),
        }


@dataclass
class EventDelivery:
    id: str
    subscription_id: str
    user_id: str
    event_id: str
    target_url: str
    payload: dict[str, Any]
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    max_attempts: int = 5
    next_attempt_at: datetime = field(default_factory=datetime.utcnow)
    last_attempt_at: datetime | None = None
    last_response_status: int | None = None
    last_error: str | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    causation_id: str | None = None
    replayed_from_dead_letter_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "event_id": self.event_id,
            "target_url": self.target_url,
            "status": self.status.value if isinstance(self.status, enum.Enum) else str(self.status),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_attempt_at": self.next_attempt_at.isoformat() if isinstance(self.next_attempt_at, datetime) else str(self.next_attempt_at),
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_response_status": self.last_response_status,
            "last_error": self.last_error,
            "causation_id": self.causation_id,
            "replayed_from_dead_letter_id": self.replayed_from_dead_letter_id,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass
class DeadLetterEvent:
    id: str
    dead_letter_type: DeadLetterType
    user_id: str
    reason: str
    final_error: str
    attempts: int
    payload: dict[str, Any]
    inbound_event_id: str | None = None
    delivery_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dead_letter_type": self.dead_letter_type.value if isinstance(self.dead_letter_type, enum.Enum) else str(self.dead_letter_type),
            "user_id": self.user_id,
            "inbound_event_id": self.inbound_event_id,
            "delivery_id": self.delivery_id,
            "reason": self.reason,
            "final_error": self.final_error,
            "attempts": self.attempts,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
        }


@dataclass
class DeadLetterReplay:
    id: str
    dead_letter_id: str
    new_delivery_id: str
    user_id: str
    replayed_by: str
    replayed_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dead_letter_id": self.dead_letter_id,
            "new_delivery_id": self.new_delivery_id,
            "user_id": self.user_id,
            "replayed_by": self.replayed_by,
            "replayed_at": self.replayed_at.isoformat() if isinstance(self.replayed_at, datetime) else str(self.replayed_at),
            "reason": self.reason,
        }


@dataclass
class MaintenanceReport:
    pruned_dead_letters: int = 0
    reclaimed_inbound_leases: int = 0
    reclaimed_delivery_leases: int = 0
    reconciled_capacities: int = 0
    success: bool = True
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def pruned_inbound_count(self) -> int:
        return self.pruned_dead_letters

    @property
    def pruned_delivery_count(self) -> int:
        return self.pruned_dead_letters

    @property
    def pruned_deliveries_count(self) -> int:
        return self.pruned_dead_letters

    def to_dict(self) -> dict[str, Any]:
        return {
            "pruned_dead_letters": self.pruned_dead_letters,
            "reclaimed_inbound_leases": self.reclaimed_inbound_leases,
            "reclaimed_delivery_leases": self.reclaimed_delivery_leases,
            "reconciled_capacities": self.reconciled_capacities,
            "timestamp": self.timestamp.isoformat(),
        }
