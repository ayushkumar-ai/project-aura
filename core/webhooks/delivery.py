"""M54 — Outbound Webhook Delivery Worker & Retry Orchestration."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from core.repositories.base_webhook import BaseWebhookRepository

from core.webhooks.crypto import (
    compute_payload_sha256,
    create_lineage_token,
    generate_hmac_signature,
)
from core.webhooks.transport import (
    PinnedIPTransport,
    calculate_backoff_delay,
    validate_outbound_target,
)
from core.webhooks.types import DeliveryStatus

logger = logging.getLogger("aura.webhooks.delivery")

MAX_OUTBOUND_LIFETIME_SECONDS = 86400  # 24 hours


import threading


class OutboundDeliveryWorker:
    """Delivers queued outbound webhooks to external targets via PinnedIPTransport with retries and dead-lettering."""

    def __init__(
        self,
        webhook_repo: BaseWebhookRepository,
        worker_id: str = "m54_delivery_worker",
        master_key: bytes | None = None,
        concurrency: int = 4,
        poll_interval: float = 0.5,
    ) -> None:
        self.webhook_repo = webhook_repo
        self.worker_id = worker_id
        self.master_key = master_key
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self._running = False
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start delivery worker background polling threads."""
        if self._running:
            return
        self._running = True
        for i in range(self.concurrency):
            t = threading.Thread(target=self._worker_loop, name=f"delivery-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        logger.info(f"Started OutboundDeliveryWorker with {self.concurrency} threads")

    def stop(self) -> None:
        """Stop delivery worker background polling threads."""
        self._running = False
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        logger.info("Stopped OutboundDeliveryWorker")

    def _worker_loop(self) -> None:
        while self._running:
            try:
                dispatched = self.deliver_batch(limit=10)
                if dispatched == 0:
                    time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Error in delivery worker loop: {e}")
                time.sleep(self.poll_interval)

    def deliver_batch(self, limit: int = 50) -> int:
        """Claim and deliver up to `limit` due outbound deliveries."""
        deliveries = self.webhook_repo.lease_due_deliveries(limit=limit, worker_id=self.worker_id, lease_seconds=30)
        delivered_count = 0

        for delivery in deliveries:
            try:
                self._execute_delivery(delivery)
                delivered_count += 1
            except Exception as e:
                logger.warning(f"Delivery {delivery.id} encountered error: {e}")

        return delivered_count

    def _execute_delivery(self, delivery: Any) -> None:
        """Perform SSRF validation, pinned socket connection, request execution, and response classification."""
        now = datetime.utcnow()
        current_ts = int(now.timestamp())

        # Check delivery lifetime (24 hours)
        age = (now - delivery.created_at).total_seconds() if hasattr(delivery.created_at, "timestamp") else 0
        if age > MAX_OUTBOUND_LIFETIME_SECONDS or delivery.attempts >= delivery.max_attempts:
            reason = "delivery_lifetime_expired" if age > MAX_OUTBOUND_LIFETIME_SECONDS else "max_attempts_exceeded"
            self.webhook_repo.mark_delivery_dead_lettered(
                delivery.id,
                reason=reason,
                last_error=f"Delivery expired or exceeded max attempts ({delivery.attempts}/{delivery.max_attempts})",
                lease_token=delivery.lease_token,
            )
            return

        # 1. SSRF & Port Validation
        try:
            hostname, port, resolved_ips = validate_outbound_target(delivery.target_url)
        except Exception as e:
            # SSRF / Port / DNS violations are non-retryable fatal errors
            self.webhook_repo.mark_delivery_failed(
                delivery.id,
                last_error=f"Target validation rejected: {e}",
                status_code=None,
                lease_token=delivery.lease_token,
            )
            return

        pinned_ip = resolved_ips[0]

        # 2. Prepare Payload & Headers
        raw_body = json.dumps(delivery.payload, sort_keys=True).encode("utf-8")
        timestamp = current_ts

        # Fetch subscription signing secret if available
        sub = self.webhook_repo.get_subscription(delivery.subscription_id, user_id=delivery.user_id)
        secret = "aura_outbound_default_secret"
        if sub and sub.signing_secret_encrypted:
            secret = sub.signing_secret_encrypted.get("secret", secret)

        signature = generate_hmac_signature(raw_body, timestamp, secret)
        lineage_token = create_lineage_token(
            user_id=delivery.user_id,
            root_event_id=delivery.event_id,
            causation_id=delivery.causation_id,
            depth=1,
            master_key=self.master_key,
        )

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AURA-Webhook-Gateway/1.0",
            "X-AURA-Event-Id": delivery.event_id,
            "X-AURA-Delivery-Id": delivery.id,
            "Idempotency-Key": delivery.id,  # Stable across all retries of the same delivery
            "X-AURA-Timestamp": str(timestamp),
            "X-AURA-Signature-256": f"t={timestamp},v1={signature}",
            "X-AURA-Lineage-Token": lineage_token,
        }

        # 3. Direct Socket Execution via PinnedIPTransport
        transport = PinnedIPTransport(pinned_ip=pinned_ip, hostname=hostname, port=port, timeout=10.0)
        try:
            status_code, resp_headers, resp_body = transport.send_request("POST", "/", raw_body, headers)
        except Exception as e:
            # Network timeout / socket reset -> Retryable
            if delivery.attempts >= delivery.max_attempts:
                self.webhook_repo.mark_delivery_dead_lettered(
                    delivery.id,
                    reason="max_attempts_exceeded",
                    last_error=f"Network error on final attempt: {e}",
                    lease_token=delivery.lease_token,
                )
            else:
                delay = calculate_backoff_delay(delivery.attempts)
                next_attempt = now + timedelta(seconds=delay)
                self.webhook_repo.mark_delivery_retrying(
                    delivery.id,
                    next_attempt_at=next_attempt,
                    last_error=f"Network error: {e}",
                    lease_token=delivery.lease_token,
                )
            return

        # 4. Response Classification
        if 200 <= status_code < 300:
            # 2xx OK -> Delivered (Terminal)
            self.webhook_repo.mark_delivery_delivered(delivery.id, status_code=status_code, lease_token=delivery.lease_token)
            return

        if 300 <= status_code < 400 or (400 <= status_code <= 422 and status_code not in (408, 409)):
            # 3xx Redirect or non-retryable 4xx -> Failed (Terminal)
            self.webhook_repo.mark_delivery_failed(
                delivery.id,
                last_error=f"HTTP {status_code} Non-retryable error",
                status_code=status_code,
                lease_token=delivery.lease_token,
            )
            return

        # 408, 409, 429, 5xx -> Retryable
        if delivery.attempts >= delivery.max_attempts:
            self.webhook_repo.mark_delivery_dead_lettered(
                delivery.id,
                reason="max_attempts_exceeded",
                last_error=f"HTTP {status_code} on final attempt",
                status_code=status_code,
                lease_token=delivery.lease_token,
            )
        else:
            retry_after = resp_headers.get("retry-after")
            delay = calculate_backoff_delay(delivery.attempts, retry_after_header=retry_after)
            next_attempt = now + timedelta(seconds=delay)
            self.webhook_repo.mark_delivery_retrying(
                delivery.id,
                next_attempt_at=next_attempt,
                last_error=f"HTTP {status_code}",
                status_code=status_code,
                lease_token=delivery.lease_token,
            )
