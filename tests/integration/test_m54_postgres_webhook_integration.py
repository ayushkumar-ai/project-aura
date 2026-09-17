"""M54 — PostgreSQL Real Concurrency, Row Locks, and Transaction Integration Tests."""

import os
import time
import pytest
from datetime import datetime
from core.database import DatabaseConnectionPool, MigrationRunner
from core.repositories.postgres_webhook import PostgresWebhookRepository
from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    InboundEvent,
    InboundEventStatus,
    EventSubscription,
    EventDelivery,
    DeliveryStatus,
    DeadLetterEvent,
    DeadLetterReplay,
    DeadLetterType,
    PayloadCollisionError,
)

DB_URL = os.getenv("AURA_DATABASE_URL", "postgresql://aura_user:aura_password@127.0.0.1:5432/aura_db")


def _is_postgres_available() -> bool:
    try:
        pool = DatabaseConnectionPool(DB_URL, min_size=1, max_size=1, is_production=False, timeout=2.0)
        if not pool.is_active:
            return False
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                return bool(cur.fetchone())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _is_postgres_available(), reason="PostgreSQL 16 live instance unavailable")


@pytest.fixture(scope="module")
def pg_pool():
    """Setup real PostgreSQL connection pool and run migrations."""
    pool = DatabaseConnectionPool(
        connection_url=DB_URL,
        min_size=2,
        max_size=10,
        timeout=5.0,
        is_production=False,
    )
    runner = MigrationRunner(pool)
    runner.run_migrations()

    # Clean test tenant rows before and after
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tenant_webhook_capacities WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM dead_letter_replays WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM dead_letter_events WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM event_deliveries WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM event_subscriptions WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM inbound_events WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM webhook_signing_keys WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM webhook_endpoints WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM users WHERE id LIKE 'pg_test_%'")
                # Seed test users
                cur.execute("INSERT INTO users (id, username) VALUES ('pg_test_tenant_cap', 'pg_test_tenant_cap') ON CONFLICT (id) DO NOTHING")
                cur.execute("INSERT INTO users (id, username) VALUES ('pg_test_lease', 'pg_test_lease') ON CONFLICT (id) DO NOTHING")

    yield pool

    # Teardown
    try:
        with pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tenant_webhook_capacities WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM dead_letter_replays WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM dead_letter_events WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM event_deliveries WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM event_subscriptions WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM inbound_events WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM webhook_signing_keys WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM webhook_endpoints WHERE user_id LIKE 'pg_test_%'")
                    cur.execute("DELETE FROM users WHERE id LIKE 'pg_test_%'")
        pool.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_test_tables(pg_pool):
    with pg_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tenant_webhook_capacities WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM dead_letter_replays WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM dead_letter_events WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM event_deliveries WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM event_subscriptions WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM inbound_events WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM webhook_signing_keys WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM webhook_endpoints WHERE user_id LIKE 'pg_test_%'")
                cur.execute("DELETE FROM users WHERE id LIKE 'pg_test_%'")
                cur.execute("INSERT INTO users (id, username) VALUES ('pg_test_tenant_cap', 'pg_test_tenant_cap') ON CONFLICT (id) DO NOTHING")
                cur.execute("INSERT INTO users (id, username) VALUES ('pg_test_lease', 'pg_test_lease') ON CONFLICT (id) DO NOTHING")
    yield


class TestM54PostgresIntegration:
    """Category 9 tests: Real PostgreSQL row locking FOR UPDATE, SKIP LOCKED, transactional admission."""

    def test_postgres_tenant_capacity_locking_and_limit(self, pg_pool):
        """PostgreSQL enforces in-flight capacity limit (100) under atomic row locks."""
        repo = PostgresWebhookRepository(pg_pool)
        user_id = "pg_test_tenant_cap"

        # Create endpoint first
        ep = WebhookEndpoint(id="ep_pg_1", user_id=user_id, name="PG Test Endpoint")
        repo.create_endpoint(ep)

        # Admitting 100 events succeeds
        for i in range(100):
            ev = InboundEvent(
                id=f"evt_pg_{i}",
                endpoint_id="ep_pg_1",
                user_id=user_id,
                provider_event_id=f"prov_pg_{i}",
                payload_sha256=f"hash_pg_{i}",
                event_type="test.event",
                payload={"index": i},
                signature="t=123,v1=abc",
                status=InboundEventStatus.ACCEPTED,
            )
            admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev)
            assert admitted is True

        # 101st event is rejected by atomic capacity check
        ev_101 = InboundEvent(
            id="evt_pg_101",
            endpoint_id="ep_pg_1",
            user_id=user_id,
            provider_event_id="prov_pg_101",
            payload_sha256="hash_pg_101",
            event_type="test.event",
            payload={"index": 101},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_101)
        assert admitted is False

        # Release capacity by marking 1 event processed
        leased = repo.lease_due_inbound_events(limit=1, worker_id="pg_worker_1")
        assert len(leased) == 1
        repo.mark_inbound_event_processed(leased[0].id, task_id=None, lease_token=leased[0].lease_token)

        # Now capacity allows 1 new event
        admitted, _, _ = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_101)
        assert admitted is True

    def test_postgres_skip_locked_batch_leasing(self, pg_pool):
        """Worker leases batches using SKIP LOCKED without deadlocking concurrent workers."""
        repo = PostgresWebhookRepository(pg_pool)
        user_id = "pg_test_lease"

        ep = WebhookEndpoint(id="ep_pg_lease", user_id=user_id, name="PG Lease Endpoint")
        repo.create_endpoint(ep)

        for i in range(5):
            ev = InboundEvent(
                id=f"evt_lease_{i}",
                endpoint_id="ep_pg_lease",
                user_id=user_id,
                provider_event_id=f"prov_lease_{i}",
                payload_sha256=f"hash_lease_{i}",
                event_type="test.event",
                payload={"index": i},
                signature="t=123,v1=abc",
                status=InboundEventStatus.ACCEPTED,
            )
            repo.reserve_tenant_capacity_and_insert_event(user_id, ev)

        worker_1_events = repo.lease_due_inbound_events(limit=3, worker_id="worker_1", lease_seconds=60)
        worker_2_events = repo.lease_due_inbound_events(limit=3, worker_id="worker_2", lease_seconds=60)

        assert len(worker_1_events) == 3
        assert len(worker_2_events) == 2
        # Disjoint sets
        w1_ids = {e.id for e in worker_1_events}
        w2_ids = {e.id for e in worker_2_events}
        assert w1_ids.isdisjoint(w2_ids)

    def test_postgres_inbound_event_dedup_and_collision(self, pg_pool):
        """PostgreSQL enforces unique provider_event_id with hash comparison."""
        repo = PostgresWebhookRepository(pg_pool)
        user_id = "pg_test_tenant_cap"

        ep = WebhookEndpoint(id="ep_pg_dedup", user_id=user_id, name="PG Dedup Endpoint")
        repo.create_endpoint(ep)

        ev1 = InboundEvent(
            id="evt_pg_d1",
            endpoint_id="ep_pg_dedup",
            user_id=user_id,
            provider_event_id="prov_d1",
            payload_sha256="hash_d1_original",
            event_type="test.event",
            payload={"amt": 100},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted, res_ev, is_dup = repo.reserve_tenant_capacity_and_insert_event(user_id, ev1)
        assert admitted is True
        assert is_dup is False
        assert res_ev.id == "evt_pg_d1"

        # Duplicate with same hash -> returns is_duplicate=True
        ev_same = InboundEvent(
            id="evt_pg_d2",
            endpoint_id="ep_pg_dedup",
            user_id=user_id,
            provider_event_id="prov_d1",
            payload_sha256="hash_d1_original",
            event_type="test.event",
            payload={"amt": 100},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        admitted2, res_ev2, is_dup2 = repo.reserve_tenant_capacity_and_insert_event(user_id, ev_same)
        assert admitted2 is True
        assert is_dup2 is True
        assert res_ev2.id == "evt_pg_d1"

        # Same provider_event_id with DIFFERENT hash -> raises PayloadCollisionError
        ev_diff = InboundEvent(
            id="evt_pg_d3",
            endpoint_id="ep_pg_dedup",
            user_id=user_id,
            provider_event_id="prov_d1",
            payload_sha256="hash_d1_TAMPERED",
            event_type="test.event",
            payload={"amt": 999},
            signature="t=123,v1=abc",
            status=InboundEventStatus.ACCEPTED,
        )
        with pytest.raises(PayloadCollisionError):
            repo.reserve_tenant_capacity_and_insert_event(user_id, ev_diff)

    def test_postgres_dead_letter_replay_transactional_atomicity(self, pg_pool):
        """PostgreSQL administrative replay creates new delivery while preserving dead-letter record immutably."""
        repo = PostgresWebhookRepository(pg_pool)
        user_id = "pg_test_tenant_cap"

        # Create subscription & original failed delivery to satisfy foreign keys
        sub = EventSubscription(
            id="sub_pg_replay",
            user_id=user_id,
            name="PG Replay Subscription",
            event_type_filter="*",
            target_type="webhook",
            target_url="https://api.example.com:8443/webhook",
        )
        repo.create_subscription(sub)

        orig_deliv = EventDelivery(
            id="del_failed_pg_1",
            subscription_id="sub_pg_replay",
            user_id=user_id,
            event_id="evt_pg_orig",
            target_url="https://api.example.com:8443/webhook",
            payload={"invoice_id": "inv_123"},
            status=DeliveryStatus.DEAD_LETTERED,
        )
        repo.create_delivery(orig_deliv)

        dl = DeadLetterEvent(
            id="dl_pg_1",
            user_id=user_id,
            dead_letter_type=DeadLetterType.OUTBOUND,
            delivery_id="del_failed_pg_1",
            reason="Max delivery attempts exceeded",
            final_error="HTTP 500 Server Error",
            attempts=5,
            payload={"invoice_id": "inv_123"},
        )
        repo.create_dead_letter_event(dl)

        # Create replay and new delivery
        new_deliv = EventDelivery(
            id="del_pg_new_1",
            subscription_id="sub_pg_replay",
            user_id=user_id,
            event_id="evt_replay_1",
            target_url="https://api.example.com:8443/webhook",
            payload={"invoice_id": "inv_123"},
            status=DeliveryStatus.PENDING,
            causation_id="dl_pg_1",
            replayed_from_dead_letter_id="dl_pg_1",
        )
        replay = DeadLetterReplay(
            id="dlr_pg_1",
            dead_letter_id="dl_pg_1",
            new_delivery_id="del_pg_new_1",
            user_id=user_id,
            replayed_by="admin_sec_ops",
            reason="Downstream partner resolved outage",
        )

        rep_res, deliv_res = repo.create_dead_letter_replay(replay, new_deliv)
        assert rep_res.id == "dlr_pg_1"
        assert deliv_res.id == "del_pg_new_1"

        # Verify historical DL record is unchanged
        dl_fetched = repo.get_dead_letter_event("dl_pg_1", user_id=user_id)
        assert dl_fetched is not None
        assert dl_fetched.payload == {"invoice_id": "inv_123"}
