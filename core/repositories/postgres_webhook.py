"""M54 — PostgreSQL Implementation of Webhook & Event Gateway Repository."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from core.database import DatabaseConnectionPool
from core.repositories.base_webhook import BaseWebhookRepository
from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    InboundEvent,
    EventSubscription,
    EventDelivery,
    DeadLetterEvent,
    DeadLetterReplay,
    TenantWebhookCapacity,
    MaintenanceReport,
    WebhookEndpointStatus,
    WebhookKeyStatus,
    InboundEventStatus,
    SubscriptionTargetType,
    SubscriptionStatus,
    DeliveryStatus,
    DeadLetterType,
    PayloadCollisionError,
    TenantCapacityExceededError,
)

logger = logging.getLogger("aura.repositories.postgres_webhook")


class PostgresWebhookRepository(BaseWebhookRepository):
    """Production-grade PostgreSQL repository implementing all M54 webhook contracts."""

    def __init__(self, db_pool: DatabaseConnectionPool) -> None:
        self.pool = db_pool

    # --- Endpoints ---
    def create_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO webhook_endpoints (
                            id, user_id, name, description, status, path_suffix,
                            allowed_event_types, rate_limit_per_minute, metadata,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            endpoint.id,
                            endpoint.user_id,
                            endpoint.name,
                            endpoint.description,
                            endpoint.status.value if isinstance(endpoint.status, WebhookEndpointStatus) else endpoint.status,
                            endpoint.path_suffix,
                            json.dumps(endpoint.allowed_event_types),
                            endpoint.rate_limit_per_minute,
                            json.dumps(endpoint.metadata),
                            endpoint.created_at,
                            endpoint.updated_at,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_endpoint(row)

    def get_endpoint(self, endpoint_id: str, user_id: str | None = None) -> WebhookEndpoint | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM webhook_endpoints WHERE id = %s AND user_id = %s;", (endpoint_id, user_id))
                else:
                    cur.execute("SELECT * FROM webhook_endpoints WHERE id = %s;", (endpoint_id,))
                row = cur.fetchone()
                return self._row_to_endpoint(row) if row else None

    def get_endpoint_by_path(self, user_id: str, path_suffix: str) -> WebhookEndpoint | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM webhook_endpoints WHERE user_id = %s AND path_suffix = %s;", (user_id, path_suffix))
                row = cur.fetchone()
                return self._row_to_endpoint(row) if row else None

    def list_endpoints(self, user_id: str) -> list[WebhookEndpoint]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM webhook_endpoints WHERE user_id = %s ORDER BY created_at ASC;", (user_id,))
                return [self._row_to_endpoint(r) for r in cur.fetchall()]

    def update_endpoint_status(self, endpoint_id: str, user_id: str, status: WebhookEndpointStatus) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    st = status.value if isinstance(status, WebhookEndpointStatus) else status
                    cur.execute(
                        "UPDATE webhook_endpoints SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s;",
                        (st, endpoint_id, user_id),
                    )
                    return cur.rowcount > 0

    def delete_endpoint(self, endpoint_id: str, user_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM webhook_endpoints WHERE id = %s AND user_id = %s;", (endpoint_id, user_id))
                    return cur.rowcount > 0

    # --- Signing Keys ---
    def create_signing_key(self, key: WebhookSigningKey) -> WebhookSigningKey:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    st = key.key_status.value if isinstance(key.key_status, WebhookKeyStatus) else key.key_status
                    cur.execute(
                        """
                        INSERT INTO webhook_signing_keys (
                            id, endpoint_id, user_id, key_version, encrypted_secret,
                            key_status, created_at, expires_at, revoked_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            key.id,
                            key.endpoint_id,
                            key.user_id,
                            key.key_version,
                            json.dumps(key.encrypted_secret),
                            st,
                            key.created_at,
                            key.expires_at,
                            key.revoked_at,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_signing_key(row)

    def get_signing_keys_for_endpoint(self, endpoint_id: str, active_only: bool = True) -> list[WebhookSigningKey]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if active_only:
                    cur.execute(
                        """
                        SELECT * FROM webhook_signing_keys
                        WHERE endpoint_id = %s
                          AND (key_status = 'active' OR (key_status = 'retiring' AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)))
                        ORDER BY key_version DESC;
                        """,
                        (endpoint_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM webhook_signing_keys WHERE endpoint_id = %s ORDER BY key_version DESC;",
                        (endpoint_id,),
                    )
                return [self._row_to_signing_key(r) for r in cur.fetchall()]

    def rotate_signing_key(
        self, endpoint_id: str, user_id: str, new_key: WebhookSigningKey
    ) -> tuple[WebhookSigningKey, WebhookSigningKey | None]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Verify endpoint ownership
                    cur.execute("SELECT id FROM webhook_endpoints WHERE id = %s AND user_id = %s;", (endpoint_id, user_id))
                    if not cur.fetchone():
                        raise ValueError("Endpoint not found or unauthorized")

                    # Mark current active keys as retiring
                    cur.execute(
                        """
                        UPDATE webhook_signing_keys
                        SET key_status = 'retiring', expires_at = CURRENT_TIMESTAMP + interval '24 hours'
                        WHERE endpoint_id = %s AND key_status = 'active'
                        RETURNING *;
                        """,
                        (endpoint_id,),
                    )
                    retired_row = cur.fetchone()
                    old_active = self._row_to_signing_key(retired_row) if retired_row else None

                    # Insert new active key
                    st = new_key.key_status.value if isinstance(new_key.key_status, WebhookKeyStatus) else new_key.key_status
                    cur.execute(
                        """
                        INSERT INTO webhook_signing_keys (
                            id, endpoint_id, user_id, key_version, encrypted_secret,
                            key_status, created_at, expires_at, revoked_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            new_key.id,
                            new_key.endpoint_id,
                            new_key.user_id,
                            new_key.key_version,
                            json.dumps(new_key.encrypted_secret),
                            st,
                            new_key.created_at,
                            new_key.expires_at,
                            new_key.revoked_at,
                        ),
                    )
                    new_row = cur.fetchone()
                    return self._row_to_signing_key(new_row), old_active

    def revoke_signing_key(self, key_id: str, user_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE webhook_signing_keys SET key_status = 'revoked', revoked_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s;",
                        (key_id, user_id),
                    )
                    return cur.rowcount > 0

    # --- Inbound Events & Tenant Admission ---
    def find_inbound_event_by_provider_id(self, endpoint_id: str, provider_event_id: str) -> InboundEvent | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM inbound_events WHERE endpoint_id = %s AND provider_event_id = %s;",
                    (endpoint_id, provider_event_id),
                )
                row = cur.fetchone()
                return self._row_to_inbound_event(row) if row else None

    def reserve_tenant_capacity_and_insert_event(
        self, user_id: str, event: InboundEvent
    ) -> tuple[bool, InboundEvent | None, bool]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 1. Check for duplicate provider_event_id
                    cur.execute(
                        "SELECT * FROM inbound_events WHERE endpoint_id = %s AND provider_event_id = %s FOR UPDATE;",
                        (event.endpoint_id, event.provider_event_id),
                    )
                    existing_row = cur.fetchone()
                    if existing_row:
                        existing = self._row_to_inbound_event(existing_row)
                        if existing.payload_sha256 == event.payload_sha256:
                            # Exact duplicate: return cached accepted event with duplicate=True
                            return True, existing, True
                        else:
                            # Collision: same ID, different payload hash -> reject with 409 Conflict
                            raise PayloadCollisionError(
                                f"Duplicate provider_event_id '{event.provider_event_id}' with mismatched payload hash"
                            )

                    # 2. Ensure tenant capacity row exists
                    cur.execute(
                        """
                        INSERT INTO tenant_webhook_capacities (user_id, in_flight_count, max_capacity, updated_at)
                        VALUES (%s, 0, 100, CURRENT_TIMESTAMP)
                        ON CONFLICT (user_id) DO NOTHING;
                        """,
                        (user_id,),
                    )

                    # 3. Lock tenant capacity row exclusively
                    cur.execute(
                        "SELECT in_flight_count, max_capacity FROM tenant_webhook_capacities WHERE user_id = %s FOR UPDATE;",
                        (user_id,),
                    )
                    cap_row = cur.fetchone()
                    if not cap_row:
                        return False, None, False

                    in_flight = cap_row["in_flight_count"]
                    max_cap = cap_row["max_capacity"]
                    if in_flight >= max_cap:
                        return False, None, False

                    # 4. Increment capacity and insert event atomically
                    cur.execute(
                        "UPDATE tenant_webhook_capacities SET in_flight_count = in_flight_count + 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s;",
                        (user_id,),
                    )

                    st = event.status.value if isinstance(event.status, InboundEventStatus) else event.status
                    cur.execute(
                        """
                        INSERT INTO inbound_events (
                            id, endpoint_id, user_id, provider_event_id, payload_sha256,
                            event_type, status, attempts, max_attempts, next_attempt_at,
                            last_attempt_at, payload, headers, signature, received_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            event.id,
                            event.endpoint_id,
                            event.user_id,
                            event.provider_event_id,
                            event.payload_sha256,
                            event.event_type,
                            st,
                            event.attempts,
                            event.max_attempts,
                            event.next_attempt_at,
                            event.last_attempt_at,
                            json.dumps(event.payload),
                            json.dumps(event.headers),
                            event.signature,
                            event.received_at,
                        ),
                    )
                    row = cur.fetchone()
                    return True, self._row_to_inbound_event(row), False

    def get_inbound_event(self, event_id: str, user_id: str | None = None) -> InboundEvent | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM inbound_events WHERE id = %s AND user_id = %s;", (event_id, user_id))
                else:
                    cur.execute("SELECT * FROM inbound_events WHERE id = %s;", (event_id,))
                row = cur.fetchone()
                return self._row_to_inbound_event(row) if row else None

    def lease_due_inbound_events(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 60
    ) -> list[InboundEvent]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id FROM inbound_events
                        WHERE status IN ('accepted', 'failed')
                          AND next_attempt_at <= CURRENT_TIMESTAMP
                          AND (lease_expires_at IS NULL OR lease_expires_at < CURRENT_TIMESTAMP)
                        ORDER BY next_attempt_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED;
                        """,
                        (limit,),
                    )
                    ids = [r["id"] for r in cur.fetchall()]
                    if not ids:
                        return []

                    token = str(uuid.uuid4())
                    cur.execute(
                        """
                        UPDATE inbound_events
                        SET status = 'processing',
                            attempts = attempts + 1,
                            last_attempt_at = CURRENT_TIMESTAMP,
                            lease_owner = %s,
                            lease_token = %s,
                            claimed_at = CURRENT_TIMESTAMP,
                            lease_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval
                        WHERE id = ANY(%s)
                        RETURNING *;
                        """,
                        (worker_id, token, lease_seconds, ids),
                    )
                    return [self._row_to_inbound_event(r) for r in cur.fetchall()]

    def mark_inbound_event_processed(
        self, event_id: str, task_id: str | None = None, lease_token: str | None = None
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'processed',
                                task_id = COALESCE(%s, task_id),
                                processed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed')
                              AND lease_token = %s
                            RETURNING user_id;
                            """,
                            (task_id, event_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'processed',
                                task_id = COALESCE(%s, task_id),
                                processed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed')
                            RETURNING user_id;
                            """,
                            (task_id, event_id),
                        )

                    row = cur.fetchone()
                    if row:
                        user_id = row["user_id"]
                        # Exactly-once capacity release
                        cur.execute(
                            """
                            UPDATE tenant_webhook_capacities
                            SET in_flight_count = GREATEST(0, in_flight_count - 1),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE user_id = %s;
                            """,
                            (user_id,),
                        )
                        return True
                    return False

    def mark_inbound_event_failed(
        self, event_id: str, error_message: str, next_attempt_at: datetime, lease_token: str | None = None
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'failed',
                                error_message = %s,
                                next_attempt_at = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed')
                              AND lease_token = %s;
                            """,
                            (error_message, next_attempt_at, event_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'failed',
                                error_message = %s,
                                next_attempt_at = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed');
                            """,
                            (error_message, next_attempt_at, event_id),
                        )
                    return cur.rowcount > 0

    def mark_inbound_event_dead_lettered(
        self, event_id: str, error_message: str, lease_token: str | None = None
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'dead_lettered',
                                error_message = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed')
                              AND lease_token = %s
                            RETURNING user_id, attempts, payload;
                            """,
                            (error_message, event_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE inbound_events
                            SET status = 'dead_lettered',
                                error_message = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('accepted', 'processing', 'failed')
                            RETURNING user_id, attempts, payload;
                            """,
                            (error_message, event_id),
                        )

                    row = cur.fetchone()
                    if row:
                        user_id = row["user_id"]
                        attempts = row["attempts"]
                        payload = row["payload"]

                        # Insert into unified dead-letter ledger
                        dl_id = f"dl_{uuid.uuid4()}"
                        cur.execute(
                            """
                            INSERT INTO dead_letter_events (
                                id, dead_letter_type, inbound_event_id, user_id,
                                reason, final_error, attempts, payload, created_at
                            ) VALUES (%s, 'inbound', %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
                            """,
                            (dl_id, event_id, user_id, "max_dispatch_attempts_exceeded", error_message, attempts, json.dumps(payload)),
                        )

                        # Exactly-once capacity release
                        cur.execute(
                            """
                            UPDATE tenant_webhook_capacities
                            SET in_flight_count = GREATEST(0, in_flight_count - 1),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE user_id = %s;
                            """,
                            (user_id,),
                        )
                        return True
                    return False

    # --- Subscriptions ---
    def create_subscription(self, subscription: EventSubscription) -> EventSubscription:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    tt = subscription.target_type.value if isinstance(subscription.target_type, SubscriptionTargetType) else subscription.target_type
                    st = subscription.status.value if isinstance(subscription.status, SubscriptionStatus) else subscription.status
                    cur.execute(
                        """
                        INSERT INTO event_subscriptions (
                            id, user_id, name, event_type_filter, target_type,
                            target_url, signing_secret_encrypted, status, metadata,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            subscription.id,
                            subscription.user_id,
                            subscription.name,
                            subscription.event_type_filter,
                            tt,
                            subscription.target_url,
                            json.dumps(subscription.signing_secret_encrypted) if subscription.signing_secret_encrypted else None,
                            st,
                            json.dumps(subscription.metadata),
                            subscription.created_at,
                            subscription.updated_at,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_subscription(row)

    def get_subscription(self, subscription_id: str, user_id: str | None = None) -> EventSubscription | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM event_subscriptions WHERE id = %s AND user_id = %s;", (subscription_id, user_id))
                else:
                    cur.execute("SELECT * FROM event_subscriptions WHERE id = %s;", (subscription_id,))
                row = cur.fetchone()
                return self._row_to_subscription(row) if row else None

    def list_subscriptions(self, user_id: str, event_type: str | None = None) -> list[EventSubscription]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if event_type:
                    cur.execute(
                        """
                        SELECT * FROM event_subscriptions
                        WHERE user_id = %s AND (event_type_filter = '*' OR event_type_filter = %s)
                        ORDER BY created_at ASC;
                        """,
                        (user_id, event_type),
                    )
                else:
                    cur.execute("SELECT * FROM event_subscriptions WHERE user_id = %s ORDER BY created_at ASC;", (user_id,))
                return [self._row_to_subscription(r) for r in cur.fetchall()]

    def delete_subscription(self, subscription_id: str, user_id: str) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM event_subscriptions WHERE id = %s AND user_id = %s;", (subscription_id, user_id))
                    return cur.rowcount > 0

    # --- Deliveries ---
    def create_delivery(self, delivery: EventDelivery) -> EventDelivery:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    st = delivery.status.value if isinstance(delivery.status, DeliveryStatus) else delivery.status
                    cur.execute(
                        """
                        INSERT INTO event_deliveries (
                            id, subscription_id, user_id, event_id, target_url,
                            status, attempts, max_attempts, next_attempt_at,
                            last_attempt_at, last_response_status, last_error,
                            payload, causation_id, replayed_from_dead_letter_id,
                            created_at, completed_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            delivery.id,
                            delivery.subscription_id,
                            delivery.user_id,
                            delivery.event_id,
                            delivery.target_url,
                            st,
                            delivery.attempts,
                            delivery.max_attempts,
                            delivery.next_attempt_at,
                            delivery.last_attempt_at,
                            delivery.last_response_status,
                            delivery.last_error,
                            json.dumps(delivery.payload),
                            delivery.causation_id,
                            delivery.replayed_from_dead_letter_id,
                            delivery.created_at,
                            delivery.completed_at,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_delivery(row)

    def get_delivery(self, delivery_id: str, user_id: str | None = None) -> EventDelivery | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM event_deliveries WHERE id = %s AND user_id = %s;", (delivery_id, user_id))
                else:
                    cur.execute("SELECT * FROM event_deliveries WHERE id = %s;", (delivery_id,))
                row = cur.fetchone()
                return self._row_to_delivery(row) if row else None

    def list_deliveries(self, user_id: str, limit: int = 50) -> list[EventDelivery]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM event_deliveries WHERE user_id = %s ORDER BY created_at DESC LIMIT %s;",
                    (user_id, limit),
                )
                return [self._row_to_delivery(r) for r in cur.fetchall()]

    def lease_due_deliveries(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 30
    ) -> list[EventDelivery]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id FROM event_deliveries
                        WHERE status IN ('pending', 'retrying')
                          AND next_attempt_at <= CURRENT_TIMESTAMP
                          AND (lease_expires_at IS NULL OR lease_expires_at < CURRENT_TIMESTAMP)
                        ORDER BY next_attempt_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED;
                        """,
                        (limit,),
                    )
                    ids = [r["id"] for r in cur.fetchall()]
                    if not ids:
                        return []

                    token = str(uuid.uuid4())
                    cur.execute(
                        """
                        UPDATE event_deliveries
                        SET status = 'delivering',
                            attempts = attempts + 1,
                            last_attempt_at = CURRENT_TIMESTAMP,
                            lease_owner = %s,
                            lease_token = %s,
                            claimed_at = CURRENT_TIMESTAMP,
                            lease_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval
                        WHERE id = ANY(%s)
                        RETURNING *;
                        """,
                        (worker_id, token, lease_seconds, ids),
                    )
                    return [self._row_to_delivery(r) for r in cur.fetchall()]

    def mark_delivery_delivered(
        self, delivery_id: str, status_code: int, lease_token: str | None = None
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'delivered',
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying')
                              AND lease_token = %s;
                            """,
                            (status_code, delivery_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'delivered',
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying');
                            """,
                            (status_code, delivery_id),
                        )
                    return cur.rowcount > 0

    def mark_delivery_retrying(
        self,
        delivery_id: str,
        next_attempt_at: datetime,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'retrying',
                                next_attempt_at = %s,
                                last_error = %s,
                                last_response_status = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying')
                              AND lease_token = %s;
                            """,
                            (next_attempt_at, last_error, status_code, delivery_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'retrying',
                                next_attempt_at = %s,
                                last_error = %s,
                                last_response_status = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying');
                            """,
                            (next_attempt_at, last_error, status_code, delivery_id),
                        )
                    return cur.rowcount > 0

    def mark_delivery_failed(
        self,
        delivery_id: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'failed',
                                last_error = %s,
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying')
                              AND lease_token = %s;
                            """,
                            (last_error, status_code, delivery_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'failed',
                                last_error = %s,
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying');
                            """,
                            (last_error, status_code, delivery_id),
                        )
                    return cur.rowcount > 0

    def mark_delivery_dead_lettered(
        self,
        delivery_id: str,
        reason: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    if lease_token:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'dead_lettered',
                                last_error = %s,
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying')
                              AND lease_token = %s
                            RETURNING user_id, attempts, payload;
                            """,
                            (last_error, status_code, delivery_id, lease_token),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE event_deliveries
                            SET status = 'dead_lettered',
                                last_error = %s,
                                last_response_status = %s,
                                completed_at = CURRENT_TIMESTAMP,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL
                            WHERE id = %s
                              AND status IN ('pending', 'delivering', 'retrying')
                            RETURNING user_id, attempts, payload;
                            """,
                            (last_error, status_code, delivery_id),
                        )

                    row = cur.fetchone()
                    if row:
                        user_id = row["user_id"]
                        attempts = row["attempts"]
                        payload = row["payload"]

                        dl_id = f"dl_{uuid.uuid4()}"
                        cur.execute(
                            """
                            INSERT INTO dead_letter_events (
                                id, dead_letter_type, delivery_id, user_id,
                                reason, final_error, attempts, payload, created_at
                            ) VALUES (%s, 'outbound', %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
                            """,
                            (dl_id, delivery_id, user_id, reason, last_error, attempts, json.dumps(payload)),
                        )
                        return True
                    return False

    # --- Dead Letter & Replay ---
    def create_dead_letter_event(self, dead_letter: DeadLetterEvent) -> DeadLetterEvent:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    dl_type = dead_letter.dead_letter_type.value if isinstance(dead_letter.dead_letter_type, DeadLetterType) else dead_letter.dead_letter_type
                    cur.execute(
                        """
                        INSERT INTO dead_letter_events (
                            id, dead_letter_type, inbound_event_id, delivery_id, user_id,
                            reason, final_error, attempts, payload, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            dead_letter.id,
                            dl_type,
                            dead_letter.inbound_event_id,
                            dead_letter.delivery_id,
                            dead_letter.user_id,
                            dead_letter.reason,
                            dead_letter.final_error,
                            dead_letter.attempts,
                            json.dumps(dead_letter.payload),
                            dead_letter.created_at,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_dead_letter(row)

    def get_dead_letter_event(self, dead_letter_id: str, user_id: str | None = None) -> DeadLetterEvent | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM dead_letter_events WHERE id = %s AND user_id = %s;", (dead_letter_id, user_id))
                else:
                    cur.execute("SELECT * FROM dead_letter_events WHERE id = %s;", (dead_letter_id,))
                row = cur.fetchone()
                return self._row_to_dead_letter(row) if row else None

    def list_dead_letter_events(self, user_id: str, limit: int = 50) -> list[DeadLetterEvent]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM dead_letter_events WHERE user_id = %s ORDER BY created_at DESC LIMIT %s;",
                    (user_id, limit),
                )
                return [self._row_to_dead_letter(r) for r in cur.fetchall()]

    def list_dead_letter_replays(self, dead_letter_id: str, user_id: str | None = None) -> list[DeadLetterReplay]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT * FROM dead_letter_replays WHERE dead_letter_id = %s AND user_id = %s ORDER BY replayed_at DESC;", (dead_letter_id, user_id))
                else:
                    cur.execute("SELECT * FROM dead_letter_replays WHERE dead_letter_id = %s ORDER BY replayed_at DESC;", (dead_letter_id,))
                return [self._row_to_dead_letter_replay(r) for r in cur.fetchall()]

    def create_dead_letter_replay(
        self, replay: DeadLetterReplay, new_delivery: EventDelivery
    ) -> tuple[DeadLetterReplay, EventDelivery]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM dead_letter_events WHERE id = %s;", (replay.dead_letter_id,))
                    dl_row = cur.fetchone()
                    if not dl_row:
                        raise ValueError("Dead-letter record not found")
                    if dl_row["user_id"] != replay.user_id or dl_row["user_id"] != new_delivery.user_id:
                        raise ValueError("Cross-tenant dead-letter replay unauthorized")

                    # Historical dead-letter record remains permanently immutable!
                    st = new_delivery.status.value if isinstance(new_delivery.status, DeliveryStatus) else new_delivery.status
                    cur.execute(
                        """
                        INSERT INTO event_deliveries (
                            id, subscription_id, user_id, event_id, target_url,
                            status, attempts, max_attempts, next_attempt_at,
                            payload, causation_id, replayed_from_dead_letter_id,
                            created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            new_delivery.id,
                            new_delivery.subscription_id,
                            new_delivery.user_id,
                            new_delivery.event_id,
                            new_delivery.target_url,
                            st,
                            new_delivery.attempts,
                            new_delivery.max_attempts,
                            new_delivery.next_attempt_at,
                            json.dumps(new_delivery.payload),
                            new_delivery.causation_id,
                            new_delivery.replayed_from_dead_letter_id,
                            new_delivery.created_at,
                        ),
                    )
                    deliv_row = cur.fetchone()

                    # Insert replay audit record
                    cur.execute(
                        """
                        INSERT INTO dead_letter_replays (
                            id, dead_letter_id, new_delivery_id, user_id, replayed_by, replayed_at, reason
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (
                            replay.id,
                            replay.dead_letter_id,
                            replay.new_delivery_id,
                            replay.user_id,
                            replay.replayed_by,
                            replay.replayed_at,
                            replay.reason,
                        ),
                    )
                    replay_row = cur.fetchone()

                    return self._row_to_replay(replay_row), self._row_to_delivery(deliv_row)

    # --- Capacities & Maintenance ---
    def get_tenant_capacity(self, user_id: str) -> TenantWebhookCapacity:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM tenant_webhook_capacities WHERE user_id = %s;", (user_id,))
                row = cur.fetchone()
                if row:
                    return TenantWebhookCapacity(
                        user_id=row["user_id"],
                        in_flight_count=row["in_flight_count"],
                        max_capacity=row["max_capacity"],
                        updated_at=row["updated_at"],
                    )
                return TenantWebhookCapacity(user_id=user_id, in_flight_count=0, max_capacity=100)

    def reconcile_tenant_capacity(self, user_id: str) -> int:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tenant_webhook_capacities (user_id, in_flight_count, max_capacity, updated_at)
                        VALUES (%s, 0, 100, CURRENT_TIMESTAMP)
                        ON CONFLICT (user_id) DO NOTHING;
                        """,
                        (user_id,),
                    )
                    cur.execute("SELECT in_flight_count, max_capacity FROM tenant_webhook_capacities WHERE user_id = %s FOR UPDATE;", (user_id,))
                    row = cur.fetchone()
                    current_count = row["in_flight_count"] if row else 0

                    cur.execute(
                        """
                        SELECT COUNT(*) AS active_count FROM inbound_events
                        WHERE user_id = %s AND status IN ('accepted', 'processing', 'failed');
                        """,
                        (user_id,),
                    )
                    actual_count = cur.fetchone()["active_count"]

                    diff = abs(current_count - actual_count)
                    if diff > 0:
                        cur.execute(
                            "UPDATE tenant_webhook_capacities SET in_flight_count = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s;",
                            (actual_count, user_id),
                        )
                    return diff

    def run_maintenance_cleanup(
        self, now: datetime, retention_days: int = 30, batch_limit: int = 100
    ) -> MaintenanceReport:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 1. Prune dead-letter records
                    cur.execute(
                        """
                        DELETE FROM dead_letter_events
                        WHERE id IN (
                            SELECT id FROM dead_letter_events
                            WHERE created_at < %s - (%s || ' days')::interval
                            LIMIT %s
                        );
                        """,
                        (now, retention_days, batch_limit),
                    )
                    pruned = cur.rowcount

                    # 2. Reclaim expired inbound leases
                    cur.execute(
                        """
                        UPDATE inbound_events
                        SET status = 'failed', lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                        WHERE status = 'processing' AND lease_expires_at < %s;
                        """,
                        (now,),
                    )
                    reclaimed_inbound = cur.rowcount

                    # 3. Reclaim expired delivery leases
                    cur.execute(
                        """
                        UPDATE event_deliveries
                        SET status = 'retrying', lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                        WHERE status = 'delivering' AND lease_expires_at < %s;
                        """,
                        (now,),
                    )
                    reclaimed_deliveries = cur.rowcount

                    # 4. Reconcile all tenant capacities
                    cur.execute("SELECT DISTINCT user_id FROM tenant_webhook_capacities;")
                    user_ids = [r["user_id"] for r in cur.fetchall()]
                    reconciled = 0
                    for uid in user_ids:
                        cur.execute(
                            """
                            UPDATE tenant_webhook_capacities c
                            SET in_flight_count = (
                                SELECT COUNT(*) FROM inbound_events e
                                WHERE e.user_id = c.user_id AND e.status IN ('accepted', 'processing', 'failed')
                            ),
                            updated_at = CURRENT_TIMESTAMP
                            WHERE c.user_id = %s AND c.in_flight_count != (
                                SELECT COUNT(*) FROM inbound_events e
                                WHERE e.user_id = c.user_id AND e.status IN ('accepted', 'processing', 'failed')
                            );
                            """,
                            (uid,),
                        )
                        if cur.rowcount > 0:
                            reconciled += 1

                    return MaintenanceReport(
                        pruned_dead_letters=pruned,
                        reclaimed_inbound_leases=reclaimed_inbound,
                        reclaimed_delivery_leases=reclaimed_deliveries,
                        reconciled_capacities=reconciled,
                        timestamp=now,
                    )

    # --- Helpers ---
    def _row_to_endpoint(self, row: dict[str, Any] | None) -> WebhookEndpoint:
        if not row:
            raise ValueError("Empty endpoint row")
        return WebhookEndpoint(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            path_suffix=row["path_suffix"],
            description=row.get("description", ""),
            status=WebhookEndpointStatus(row["status"]),
            allowed_event_types=json.loads(row["allowed_event_types"]) if isinstance(row["allowed_event_types"], str) else row["allowed_event_types"],
            rate_limit_per_minute=row.get("rate_limit_per_minute", 120),
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row.get("metadata", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_signing_key(self, row: dict[str, Any] | None) -> WebhookSigningKey:
        if not row:
            raise ValueError("Empty signing key row")
        return WebhookSigningKey(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            user_id=row["user_id"],
            key_version=row["key_version"],
            encrypted_secret=json.loads(row["encrypted_secret"]) if isinstance(row["encrypted_secret"], str) else row["encrypted_secret"],
            key_status=WebhookKeyStatus(row["key_status"]),
            created_at=row["created_at"],
            expires_at=row.get("expires_at"),
            revoked_at=row.get("revoked_at"),
        )

    def _row_to_inbound_event(self, row: dict[str, Any] | None) -> InboundEvent:
        if not row:
            raise ValueError("Empty inbound event row")
        return InboundEvent(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            user_id=row["user_id"],
            provider_event_id=row["provider_event_id"],
            payload_sha256=row["payload_sha256"],
            event_type=row["event_type"],
            status=InboundEventStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row.get("max_attempts", 3),
            next_attempt_at=row["next_attempt_at"],
            last_attempt_at=row.get("last_attempt_at"),
            payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            headers=json.loads(row["headers"]) if isinstance(row.get("headers"), str) else row.get("headers", {}),
            signature=row["signature"],
            lease_owner=row.get("lease_owner"),
            lease_token=row.get("lease_token"),
            claimed_at=row.get("claimed_at"),
            lease_expires_at=row.get("lease_expires_at"),
            task_id=row.get("task_id"),
            error_message=row.get("error_message"),
            received_at=row["received_at"],
            processed_at=row.get("processed_at"),
        )

    def _row_to_subscription(self, row: dict[str, Any] | None) -> EventSubscription:
        if not row:
            raise ValueError("Empty subscription row")
        return EventSubscription(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            event_type_filter=row["event_type_filter"],
            target_type=SubscriptionTargetType(row["target_type"]),
            target_url=row.get("target_url"),
            signing_secret_encrypted=json.loads(row["signing_secret_encrypted"]) if isinstance(row.get("signing_secret_encrypted"), str) else row.get("signing_secret_encrypted"),
            status=SubscriptionStatus(row["status"]),
            metadata=json.loads(row["metadata"]) if isinstance(row.get("metadata"), str) else row.get("metadata", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_delivery(self, row: dict[str, Any] | None) -> EventDelivery:
        if not row:
            raise ValueError("Empty delivery row")
        return EventDelivery(
            id=row["id"],
            subscription_id=row["subscription_id"],
            user_id=row["user_id"],
            event_id=row["event_id"],
            target_url=row["target_url"],
            status=DeliveryStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row.get("max_attempts", 5),
            next_attempt_at=row["next_attempt_at"],
            last_attempt_at=row.get("last_attempt_at"),
            last_response_status=row.get("last_response_status"),
            last_error=row.get("last_error"),
            lease_owner=row.get("lease_owner"),
            lease_token=row.get("lease_token"),
            claimed_at=row.get("claimed_at"),
            lease_expires_at=row.get("lease_expires_at"),
            payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            causation_id=row.get("causation_id"),
            replayed_from_dead_letter_id=row.get("replayed_from_dead_letter_id"),
            created_at=row["created_at"],
            completed_at=row.get("completed_at"),
        )

    def _row_to_dead_letter(self, row: dict[str, Any] | None) -> DeadLetterEvent:
        if not row:
            raise ValueError("Empty dead letter row")
        return DeadLetterEvent(
            id=row["id"],
            dead_letter_type=DeadLetterType(row["dead_letter_type"]),
            inbound_event_id=row.get("inbound_event_id"),
            delivery_id=row.get("delivery_id"),
            user_id=row["user_id"],
            reason=row["reason"],
            final_error=row["final_error"],
            attempts=row["attempts"],
            payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            created_at=row["created_at"],
        )

    def _row_to_replay(self, row: dict[str, Any] | None) -> DeadLetterReplay:
        if not row:
            raise ValueError("Empty replay row")
        return DeadLetterReplay(
            id=row["id"],
            dead_letter_id=row["dead_letter_id"],
            new_delivery_id=row["new_delivery_id"],
            user_id=row["user_id"],
            replayed_by=row["replayed_by"],
            replayed_at=row["replayed_at"],
            reason=row.get("reason", ""),
        )
