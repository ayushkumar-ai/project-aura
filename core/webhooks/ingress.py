"""M54 — Inbound Webhook Ingress Processing Service."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from core.repositories.base_webhook import BaseWebhookRepository

from core.webhooks.crypto import (
    compute_payload_sha256,
    decrypt_signing_secret,
    parse_signature_header,
    verify_hmac_signature,
    verify_lineage_token,
)
from core.webhooks.types import (
    InboundEvent,
    InboundEventStatus,
    WebhookEndpointStatus,
    WebhookSignatureError,
    WebhookTimestampError,
    PayloadCollisionError,
    TenantCapacityExceededError,
)

logger = logging.getLogger("aura.webhooks.ingress")

MAX_INBOUND_BODY_BYTES = 1048576  # 1MB
MAX_TIMESTAMP_SKEW_SECONDS = 300  # 5 minutes
MAX_FUTURE_SKEW_SECONDS = 60      # 1 minute


def process_inbound_webhook(
    endpoint_id: str,
    raw_body: bytes,
    headers: dict[str, str],
    repo: BaseWebhookRepository,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Process incoming webhook request through full authentication, validation, and admission pipeline.
    
    Returns:
        (status_code, response_dict)
    """
    if now is None:
        current_ts = int(time.time())
        now = datetime.fromtimestamp(current_ts, tz=timezone.utc).replace(tzinfo=None)
    else:
        if now.tzinfo is None:
            current_ts = int(now.replace(tzinfo=timezone.utc).timestamp())
        else:
            current_ts = int(now.timestamp())

    # 1. Request Body Size Limit (<= 1MB)
    if len(raw_body) > MAX_INBOUND_BODY_BYTES:
        return 413, {"error": "payload_too_large", "message": f"Payload exceeds {MAX_INBOUND_BODY_BYTES} byte limit"}

    # 2. Endpoint Lookup
    endpoint = repo.get_endpoint(endpoint_id)
    if not endpoint or endpoint.status != WebhookEndpointStatus.ACTIVE:
        return 404, {"error": "not_found", "message": "Webhook endpoint not found or inactive"}

    user_id = endpoint.user_id

    # 3. Parse and Validate Timestamp
    # Check headers (case-insensitive)
    norm_headers = {k.lower(): v for k, v in headers.items()}
    sig_header = norm_headers.get("x-aura-signature-256", "")
    ts_header = norm_headers.get("x-aura-timestamp", "")

    req_timestamp: int | None = None
    if ts_header:
        try:
            req_timestamp = int(ts_header)
        except ValueError:
            pass

    header_ts, signatures, kid = parse_signature_header(sig_header)
    if header_ts is not None:
        req_timestamp = header_ts

    if req_timestamp is None:
        return 401, {"error": "unauthorized", "message": "Missing timestamp in signature or headers"}

    # Tolerance window check: |t_req - t_server| <= 300s, future <= 60s
    skew = req_timestamp - current_ts
    if skew > MAX_FUTURE_SKEW_SECONDS or (current_ts - req_timestamp) > MAX_TIMESTAMP_SKEW_SECONDS:
        return 400, {"error": "timestamp_out_of_bounds", "message": f"Timestamp {req_timestamp} is outside allowable window (+-{MAX_TIMESTAMP_SKEW_SECONDS}s)"}

    # 4. Fetch Signing Keys & Decrypt Secrets
    signing_keys = repo.get_signing_keys_for_endpoint(endpoint_id, active_only=True)
    if not signing_keys:
        return 401, {"error": "unauthorized", "message": "No active signing keys available for endpoint"}

    if kid:
        signing_keys = [k for k in signing_keys if k.id == kid]
        if not signing_keys:
            return 401, {"error": "unauthorized", "message": f"Key with kid '{kid}' is revoked or invalid"}

    # Decrypt candidate secrets in-memory
    candidate_secrets: list[str] = []
    for k in signing_keys:
        try:
            sec = decrypt_signing_secret(k.encrypted_secret, endpoint_id, user_id)
            candidate_secrets.append(sec)
        except Exception as e:
            logger.warning(f"Failed to decrypt signing key {k.id}: {e}")

    if not candidate_secrets:
        return 401, {"error": "unauthorized", "message": "Failed to resolve signing secret"}

    # 5. Constant-Time HMAC Signature Verification
    if not signatures:
        return 401, {"error": "unauthorized", "message": "Missing signature in header"}

    is_valid_sig = verify_hmac_signature(raw_body, req_timestamp, signatures, candidate_secrets)
    if not is_valid_sig:
        return 401, {"error": "unauthorized", "message": "Invalid HMAC signature"}

    # 6. Parse JSON Payload
    try:
        parsed_payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(parsed_payload, dict):
            return 400, {"error": "bad_request", "message": "Payload must be a JSON object"}
    except Exception:
        return 400, {"error": "bad_request", "message": "Malformed JSON payload"}

    # 7. Compute Canonical Raw Wire SHA-256 Hash
    payload_sha256 = compute_payload_sha256(raw_body)

    # 8. Provider Event ID Extraction or 15-Minute Bucketed Synthetic Key
    provider_event_id = norm_headers.get("x-aura-event-id") or parsed_payload.get("id") or parsed_payload.get("event_id")
    if not provider_event_id:
        bucket_15min = int(current_ts // 900) * 900
        provider_event_id = f"sha256_{payload_sha256}_{bucket_15min}"

    event_type = norm_headers.get("x-aura-event-type") or parsed_payload.get("event_type") or parsed_payload.get("type") or "default"

    # 9. Verify Lineage Token (if present)
    lineage_token = norm_headers.get("x-aura-lineage-token", "")
    lineage_context = None
    if lineage_token:
        try:
            lineage_context = verify_lineage_token(lineage_token, user_id)
        except Exception as e:
            logger.warning(f"Lineage verification failed / loop detected: {e}")
            return 400, {"error": "event_loop_detected", "message": str(e)}

    # 10. Construct Inbound Event
    event_id = f"evt_{uuid.uuid4()}"
    inbound_event = InboundEvent(
        id=event_id,
        endpoint_id=endpoint_id,
        user_id=user_id,
        provider_event_id=str(provider_event_id),
        payload_sha256=payload_sha256,
        event_type=str(event_type),
        payload=parsed_payload,
        signature=sig_header,
        status=InboundEventStatus.ACCEPTED,
        attempts=0,
        max_attempts=3,
        next_attempt_at=now,
        headers=norm_headers,
        received_at=now,
    )

    # 11. Atomic Tenant Capacity Check, Dedup & Insertion
    try:
        admitted, res_event, is_duplicate = repo.reserve_tenant_capacity_and_insert_event(user_id, inbound_event)
        if not admitted:
            return 429, {"error": "tenant_in_flight_capacity_exceeded", "message": "Tenant in-flight webhook capacity exceeded (limit: 100)"}

        return 202, {
            "status": "accepted",
            "event_id": res_event.id if res_event else event_id,
            "duplicate": is_duplicate,
        }
    except PayloadCollisionError as e:
        return 409, {"error": "payload_collision_rejected", "message": str(e)}
    except Exception as e:
        logger.error(f"Inbound admission failed: {e}")
        return 500, {"error": "internal_error", "message": "Failed to persist inbound event"}


class WebhookIngressResponse:
    """Response envelope for webhook ingress operations."""

    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return self.data


class WebhookIngressService:
    """High-level service interface for managing inbound webhook processing."""

    def __init__(
        self,
        webhook_repo: BaseWebhookRepository,
        task_repo: Any = None,
        automation_repo: Any = None,
        master_key: str = "aura-default-master-key-32bytes!!",
    ) -> None:
        self.webhook_repo = webhook_repo
        self.task_repo = task_repo
        self.automation_repo = automation_repo
        self.master_key = master_key

    def handle_inbound_request(
        self,
        endpoint_id: str,
        payload_bytes: bytes,
        headers: dict[str, str],
        query_params: dict[str, Any] | None = None,
        client_ip: str = "127.0.0.1",
        now: datetime | None = None,
    ) -> WebhookIngressResponse:
        """Handle incoming raw webhook request."""
        status, data = process_inbound_webhook(
            endpoint_id=endpoint_id,
            raw_body=payload_bytes,
            headers=headers,
            repo=self.webhook_repo,
            now=now,
        )
        return WebhookIngressResponse(status_code=status, data=data)
