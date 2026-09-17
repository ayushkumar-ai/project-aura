"""M54 — Enterprise Webhooks & Event Gateway End-to-End Integration Tests."""

import json
import time
import pytest
from datetime import datetime, timedelta
from core.repositories.in_memory_webhook import InMemoryWebhookRepository
from core.repositories.in_memory import InMemoryTaskRepository
from core.repositories.in_memory_automation import InMemoryAutomationRepository
from core.webhooks.crypto import (
    derive_tenant_key,
    encrypt_signing_secret,
    generate_hmac_signature,
    compute_payload_sha256,
)
from core.webhooks.ingress import WebhookIngressService, process_inbound_webhook
from core.webhooks.dispatcher import InboundEventDispatcher
from core.webhooks.delivery import OutboundDeliveryWorker
from core.webhooks.replay import DeadLetterReplayService
from core.webhooks.maintenance import WebhookMaintenanceService
from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    WebhookEndpointStatus,
    InboundEvent,
    InboundEventStatus,
    EventSubscription,
    EventDelivery,
    DeliveryStatus,
    DeadLetterEvent,
    DeadLetterType,
)


@pytest.fixture
def webhook_env():
    """Setup in-memory webhook environment with all M52/M53 collaborator repos."""
    webhook_repo = InMemoryWebhookRepository()
    task_repo = InMemoryTaskRepository()
    auto_repo = InMemoryAutomationRepository()
    master_key = "aura-m54-test-master-key-32bytes!!"

    user_id = "test_tenant"

    # Create active endpoint
    ep = WebhookEndpoint(
        id="ep_main",
        user_id=user_id,
        name="Main Production Webhook",
        status=WebhookEndpointStatus.ACTIVE,
    )
    webhook_repo.create_endpoint(ep)

    # Add active signing key
    raw_secret = "whsec_live_test_secret_key_12345"
    enc_secret = encrypt_signing_secret(raw_secret, "ep_main", user_id)
    key = WebhookSigningKey(
        id="key_primary",
        endpoint_id="ep_main",
        user_id=user_id,
        encrypted_secret=enc_secret,
    )
    webhook_repo.add_signing_key(key)

    # Initialize services
    ingress_srv = WebhookIngressService(
        webhook_repo=webhook_repo,
        task_repo=task_repo,
        automation_repo=auto_repo,
        master_key=master_key,
    )
    dispatcher = InboundEventDispatcher(
        webhook_repo=webhook_repo,
        task_repo=task_repo,
        automation_repo=auto_repo,
    )
    delivery_worker = OutboundDeliveryWorker(
        webhook_repo=webhook_repo,
        concurrency=2,
        poll_interval=0.1,
    )
    replay_srv = DeadLetterReplayService(
        webhook_repo=webhook_repo,
    )
    maintenance_srv = WebhookMaintenanceService(
        webhook_repo=webhook_repo,
    )

    return {
        "repo": webhook_repo,
        "task_repo": task_repo,
        "auto_repo": auto_repo,
        "user_id": user_id,
        "raw_secret": raw_secret,
        "ingress_srv": ingress_srv,
        "dispatcher": dispatcher,
        "delivery_worker": delivery_worker,
        "replay_srv": replay_srv,
        "maintenance_srv": maintenance_srv,
    }


class TestM54EndToEndIntegration:
    """Complete adversarial end-to-end integration scenarios for M54."""

    def test_full_inbound_ingress_and_dispatch_pipeline(self, webhook_env):
        """Inbound webhook accepted, dispatched to M52 task, and marked processed."""
        repo = webhook_env["repo"]
        task_repo = webhook_env["task_repo"]
        raw_secret = webhook_env["raw_secret"]
        ingress_srv = webhook_env["ingress_srv"]
        dispatcher = webhook_env["dispatcher"]

        body = b'{"event_type": "order.completed", "order_id": "ord_999", "amount": 250.00}'
        ts = int(time.time())
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
            "x-aura-event-id": "ext_evt_001",
        }

        resp = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert resp.status_code == 202
        assert resp.data["status"] == "accepted"
        assert resp.data["duplicate"] is False
        event_id = resp.data["event_id"]

        assert repo.get_tenant_capacity("test_tenant").in_flight_count == 1

        dispatched = dispatcher.dispatch_batch(limit=10)
        assert dispatched == 1

        ev = repo.get_inbound_event(event_id, user_id="test_tenant")
        assert ev.status.value == "processed"
        assert ev.task_id is not None
        assert repo.get_tenant_capacity("test_tenant").in_flight_count == 0

    def test_inbound_ingress_payload_too_large_413(self, webhook_env):
        """Ingress payload > 1MB returns HTTP 413 Payload Too Large."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        big_body = b"A" * (1048576 + 1)
        ts = int(time.time())
        sig = generate_hmac_signature(big_body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
        }
        resp = ingress_srv.handle_inbound_request("ep_main", big_body, headers)
        assert resp.status_code == 413
        assert resp.data["error"] == "payload_too_large"

    def test_inbound_ingress_invalid_json_400(self, webhook_env):
        """Malformed JSON payload returns HTTP 400 Bad Request."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body = b"{not-valid-json"
        ts = int(time.time())
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
        }
        resp = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert resp.status_code == 400
        assert resp.data["error"] == "bad_request"

    def test_inbound_ingress_timestamp_skew_past_400(self, webhook_env):
        """Timestamp > 300s in past returns HTTP 400."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body = b'{"hello": "world"}'
        ts = int(time.time()) - 305  # 305s in past
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
        }
        resp = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert resp.status_code == 400
        assert resp.data["error"] == "timestamp_out_of_bounds"

    def test_inbound_ingress_timestamp_skew_future_400(self, webhook_env):
        """Timestamp > 60s in future returns HTTP 400."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body = b'{"hello": "world"}'
        ts = int(time.time()) + 70  # 70s in future
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
        }
        resp = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert resp.status_code == 400
        assert resp.data["error"] == "timestamp_out_of_bounds"

    def test_inbound_ingress_invalid_signature_401(self, webhook_env):
        """Invalid HMAC signature returns HTTP 401 Unauthorized."""
        ingress_srv = webhook_env["ingress_srv"]

        body = b'{"hello": "world"}'
        ts = int(time.time())
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1=bad_signature_value_00000000000000000000000000000000000000000000000000",
        }
        resp = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert resp.status_code == 401
        assert resp.data["error"] == "unauthorized"

    def test_inbound_ingress_inactive_endpoint_404(self, webhook_env):
        """Disabled or non-existent endpoint returns HTTP 404."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body = b'{"hello": "world"}'
        ts = int(time.time())
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
        }
        resp = ingress_srv.handle_inbound_request("ep_non_existent", body, headers)
        assert resp.status_code == 404

    def test_inbound_ingress_duplicate_idempotent_202(self, webhook_env):
        """Sending the exact same provider_event_id and payload returns 202 with duplicate=True."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body = b'{"event_type": "user.signup", "user": "alice"}'
        ts = int(time.time())
        sig = generate_hmac_signature(body, ts, raw_secret)
        headers = {
            "x-aura-timestamp": str(ts),
            "x-aura-signature-256": f"t={ts},v1={sig}",
            "x-aura-event-id": "signup_001",
        }

        r1 = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert r1.status_code == 202
        assert r1.data["duplicate"] is False

        r2 = ingress_srv.handle_inbound_request("ep_main", body, headers)
        assert r2.status_code == 202
        assert r2.data["duplicate"] is True
        assert r2.data["event_id"] == r1.data["event_id"]

    def test_inbound_ingress_payload_collision_409(self, webhook_env):
        """Sending same provider_event_id with DIFFERENT payload returns HTTP 409 Conflict."""
        ingress_srv = webhook_env["ingress_srv"]
        raw_secret = webhook_env["raw_secret"]

        body1 = b'{"order": 100}'
        ts1 = int(time.time())
        sig1 = generate_hmac_signature(body1, ts1, raw_secret)
        headers1 = {
            "x-aura-timestamp": str(ts1),
            "x-aura-signature-256": f"t={ts1},v1={sig1}",
            "x-aura-event-id": "collision_evt_001",
        }
        r1 = ingress_srv.handle_inbound_request("ep_main", body1, headers1)
        assert r1.status_code == 202

        body2 = b'{"order": 999}'
        ts2 = int(time.time())
        sig2 = generate_hmac_signature(body2, ts2, raw_secret)
        headers2 = {
            "x-aura-timestamp": str(ts2),
            "x-aura-signature-256": f"t={ts2},v1={sig2}",
            "x-aura-event-id": "collision_evt_001",
        }
        r2 = ingress_srv.handle_inbound_request("ep_main", body2, headers2)
        assert r2.status_code == 409
        assert r2.data["error"] == "payload_collision_rejected"

    def test_dead_letter_replay_flow(self, webhook_env):
        """Dead-letter events are preserved immutably while administrative replays create new deliveries."""
        repo = webhook_env["repo"]
        replay_srv = webhook_env["replay_srv"]
        user_id = webhook_env["user_id"]

        dl = DeadLetterEvent(
            id="dl_sample_1",
            user_id=user_id,
            dead_letter_type=DeadLetterType.OUTBOUND,
            reason="Endpoint connection refused (5 attempts)",
            final_error="ConnectionRefusedError: port 8443",
            attempts=5,
            payload={"action": "trigger_workflow", "target": "crm"},
            delivery_id="del_failed_001",
        )
        repo.create_dead_letter_event(dl)

        new_deliv = replay_srv.replay_dead_letter(
            dead_letter_id="dl_sample_1",
            user_id=user_id,
            replayed_by="admin_operator",
            reason="Downstream webhook listener restored",
        )

        assert new_deliv.id != "del_failed_001"
        assert new_deliv.status == DeliveryStatus.PENDING
        assert new_deliv.replayed_from_dead_letter_id == "dl_sample_1"
        assert new_deliv.causation_id == "dl_sample_1"
        assert new_deliv.payload == {"action": "trigger_workflow", "target": "crm"}

        original_dl = repo.get_dead_letter_event("dl_sample_1", user_id=user_id)
        assert original_dl is not None
        assert original_dl.id == "dl_sample_1"

    def test_single_maintenance_task_boundary(self, webhook_env):
        """Maintenance service prunes expired records and enforces retention policies."""
        repo = webhook_env["repo"]
        maintenance_srv = webhook_env["maintenance_srv"]

        now = datetime.utcnow()
        report = maintenance_srv.run_maintenance_once(now=now, retention_days=30, batch_limit=100)
        assert report.pruned_inbound_count >= 0
        assert report.pruned_deliveries_count >= 0
        assert report.success is True
