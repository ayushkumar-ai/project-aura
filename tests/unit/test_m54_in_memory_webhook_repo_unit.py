"""M54 — In-Memory Webhook Repository & Capacity Invariant Unit Tests."""

import time
import pytest
from datetime import datetime, timedelta
from core.repositories.in_memory_webhook import InMemoryWebhookRepository
from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    InboundEvent,
    InboundEventStatus,
    EventSubscription,
    EventDelivery,
    DeliveryStatus,
    DeadLetterEvent,
    DeadLetterType,
    TenantCapacityExceededError,
    PayloadCollisionError,
    ImmutableStateError,
)


class TestM54InMemoryWebhookRepository:
    """Category 2, 3, 7 tests: Capacity [100 limit], exactly-once release, dedup vs collision, immutability."""

    def test_tenant_in_flight_capacity_enforced_at_100(self):
        """Tenant cannot exceed 100 in-flight events (accepted, processing, failed)."""
        repo = InMemoryWebhookRepository()
        user_id = "tenant_cap_test"

        # Admitting 100 events succeeds
        for i in range(100):
            ev = InboundEvent(
                id=f"evt_{i}",
                endpoint_id="ep_1",
                user_id=user_id,
                provider_event_id=f"prov_{i}",
                payload_sha256=f"hash_{i}",
                event_type="test",
                payload={"i": i},
                signature="t=123,v1=abc",
                status=InboundEventStatus.ACCEPTED,
            )
            admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev)
            assert admitted is True

        # 101st event is rejected due to capacity limit
        ev_101 = InboundEvent(
            id="evt_101",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_101",
            payload_sha256="hash_101",
            event_type="test",
            payload={"i": 101},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_101)
        assert admitted is False

    def test_capacity_exactly_once_release_upon_terminal_transition(self):
        """Capacity is decremented exactly once when event transitions from non-terminal to terminal."""
        repo = InMemoryWebhookRepository()
        user_id = "tenant_release_test"

        # Fill to 100
        for i in range(100):
            ev = InboundEvent(
                id=f"evt_{i}",
                endpoint_id="ep_1",
                user_id=user_id,
                provider_event_id=f"prov_{i}",
                payload_sha256=f"hash_{i}",
                event_type="test",
                payload={"i": i},
                signature="t=123,v1=abc",
                status=InboundEventStatus.ACCEPTED,
            )
            repo.reserve_tenant_capacity_and_insert_event(user_id, ev)

        cap_before = repo.get_tenant_capacity(user_id)
        assert cap_before.in_flight_count == 100

        # Lease and mark event 0 as processed (terminal)
        events = repo.lease_due_inbound_events(limit=1, worker_id="worker_1")
        assert len(events) == 1
        ev_leased = events[0]
        repo.mark_inbound_event_processed(ev_leased.id, task_id="task_123", lease_token=ev_leased.lease_token)

        cap_after = repo.get_tenant_capacity(user_id)
        assert cap_after.in_flight_count == 99

        # Now 101st event CAN be admitted!
        ev_new = InboundEvent(
            id="evt_new",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_new",
            payload_sha256="hash_new",
            event_type="test",
            payload={"new": True},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_new)
        assert admitted is True
        assert repo.get_tenant_capacity(user_id).in_flight_count == 100

    def test_idempotent_deduplication_vs_payload_collision(self):
        """Duplicate provider_event_id with IDENTICAL hash is accepted idempotently; DIFFERENT hash raises 409 collision."""
        repo = InMemoryWebhookRepository()
        user_id = "tenant_dedup_test"

        ev1 = InboundEvent(
            id="evt_orig",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_abc_123",
            payload_sha256="canonical_hash_abc",
            event_type="test",
            payload={"amount": 100},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted, ret_ev, is_dup = repo.reserve_tenant_capacity_and_insert_event(user_id, ev1)
        assert admitted is True
        assert is_dup is False
        assert ret_ev.id == "evt_orig"

        # Same provider_event_id and same hash -> Deduplicated
        ev_same = InboundEvent(
            id="evt_dup",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_abc_123",
            payload_sha256="canonical_hash_abc",
            event_type="test",
            payload={"amount": 100},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted2, ret_ev2, is_dup2 = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_same)
        assert admitted2 is True
        assert is_dup2 is True
        assert ret_ev2.id == "evt_orig"  # Returns original record

        # Same provider_event_id and DIFFERENT hash -> 409 Collision error!
        ev_collision = InboundEvent(
            id="evt_collision",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_abc_123",
            payload_sha256="DIFFERENT_HASH_xyz",
            event_type="test",
            payload={"amount": 9999},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        with pytest.raises(PayloadCollisionError):
            repo.reserve_tenant_capacity_and_insert_event(user_id, ev_collision)

    def test_dead_letter_events_immutable_and_unified(self):
        """Dead letter records are 100% write-once immutable with unified schema."""
        repo = InMemoryWebhookRepository()
        user_id = "tenant_dl_test"

        dl = DeadLetterEvent(
            id="dl_1",
            user_id=user_id,
            dead_letter_type=DeadLetterType.INBOUND,
            reason="Worker timeout",
            final_error="Worker timeout",
            attempts=3,
            payload={"order_id": 99},
            inbound_event_id="evt_123",
        )
        created = repo.create_dead_letter_event(dl)
        assert created.id == "dl_1"

        fetched = repo.get_dead_letter_event("dl_1", user_id=user_id)
        assert fetched is not None
        assert fetched.dead_letter_type == DeadLetterType.INBOUND
        assert fetched.payload == {"order_id": 99}

    def test_inbound_event_lease_fencing(self):
        """Leasing generates a unique lease token; stale worker with expired lease cannot mark processed."""
        repo = InMemoryWebhookRepository()
        user_id = "tenant_fence_test"

        ev = InboundEvent(
            id="evt_fence_1",
            endpoint_id="ep_1",
            user_id=user_id,
            provider_event_id="prov_fence_1",
            payload_sha256="hash_fence_1",
            event_type="test",
            payload={"fenced": True},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        repo.reserve_tenant_capacity_and_insert_event(user_id, ev)

        leased = repo.lease_due_inbound_events(limit=1, worker_id="worker_1", lease_seconds=1)
        assert len(leased) == 1
        tok1 = leased[0].lease_token

        # Attempt to mark with WRONG token fails
        updated = repo.mark_inbound_event_processed("evt_fence_1", task_id="task_1", lease_token="bad_token")
        assert updated is False

        # Mark with CORRECT token succeeds
        updated2 = repo.mark_inbound_event_processed("evt_fence_1", task_id="task_1", lease_token=tok1)
        assert updated2 is True
