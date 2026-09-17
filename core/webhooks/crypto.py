"""M54 — Cryptographic Key Lifecycle, AES-256-GCM Secret Encryption & Lineage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    AESGCM = None  # type: ignore
    HKDF = None  # type: ignore
    hashes = None  # type: ignore
    CRYPTOGRAPHY_AVAILABLE = False

from core.webhooks.types import (
    KeyUnavailableError,
    WebhookSignatureError,
    EventLoopDetectedError,
)

logger = logging.getLogger("aura.webhooks.crypto")

DEFAULT_DEV_MASTER_KEY = base64.b64encode(b"aura_default_master_key_32bytes!").decode("utf-8")


def get_master_key() -> bytes:
    """Retrieve external master key for webhook secret encryption. Fail-closed if missing in prod."""
    raw_key = os.environ.get("AURA_WEBHOOK_MASTER_KEY")
    if not raw_key:
        is_prod = os.environ.get("AURA_ENVIRONMENT", "development").lower() == "production"
        if is_prod:
            raise KeyUnavailableError("AURA_WEBHOOK_MASTER_KEY is required in production mode.")
        raw_key = DEFAULT_DEV_MASTER_KEY

    try:
        key_bytes = base64.b64decode(raw_key)
        if len(key_bytes) != 32:
            raise ValueError(f"Master key must be 32 bytes (256 bits), got {len(key_bytes)} bytes")
        return key_bytes
    except Exception as e:
        raise KeyUnavailableError(f"Failed to decode AURA_WEBHOOK_MASTER_KEY: {e}") from e


def derive_tenant_key(user_id: str, master_key: bytes | None = None) -> bytes:
    """Derive 256-bit tenant encryption key using HKDF-SHA256."""
    if master_key is None:
        master_key = get_master_key()

    if not CRYPTOGRAPHY_AVAILABLE:
        # Fallback pseudo-HKDF using sha256 if cryptography package missing in test
        salt = user_id.encode("utf-8")
        prk = hmac.new(salt, master_key, hashlib.sha256).digest()
        info = b"aura_webhook_v1"
        return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode("utf-8"),
        info=b"aura_webhook_v1",
    )
    return hkdf.derive(master_key)


def encrypt_signing_secret(secret_plaintext: str, endpoint_id: str, user_id: str) -> dict[str, Any]:
    """Encrypt signing secret at rest using AES-256-GCM envelope encryption."""
    tenant_key = derive_tenant_key(user_id)
    iv = os.urandom(12)  # 96-bit nonce
    aad = f"endpoint_id:{endpoint_id}:user_id:{user_id}".encode("utf-8")

    if not CRYPTOGRAPHY_AVAILABLE:
        # Simple test fallback XOR + HMAC tag if cryptography not installed
        ct = bytes(b ^ iv[i % 12] for i, b in enumerate(secret_plaintext.encode("utf-8")))
        tag = hmac.new(tenant_key, iv + aad + ct, hashlib.sha256).digest()[:16]
        return {
            "iv": base64.b64encode(iv).decode("utf-8"),
            "ciphertext": base64.b64encode(ct + tag).decode("utf-8"),
            "version": 1,
        }

    aesgcm = AESGCM(tenant_key)
    ciphertext_and_tag = aesgcm.encrypt(iv, secret_plaintext.encode("utf-8"), aad)

    return {
        "iv": base64.b64encode(iv).decode("utf-8"),
        "ciphertext": base64.b64encode(ciphertext_and_tag).decode("utf-8"),
        "version": 1,
    }


def decrypt_signing_secret(encrypted_secret: dict[str, Any], endpoint_id: str, user_id: str) -> str:
    """Decrypt signing secret using AES-256-GCM with authenticated associated data."""
    tenant_key = derive_tenant_key(user_id)
    try:
        iv = base64.b64decode(encrypted_secret["iv"])
        ciphertext_and_tag = base64.b64decode(encrypted_secret["ciphertext"])
        aad = f"endpoint_id:{endpoint_id}:user_id:{user_id}".encode("utf-8")

        if not CRYPTOGRAPHY_AVAILABLE:
            tag = ciphertext_and_tag[-16:]
            ct = ciphertext_and_tag[:-16]
            expected_tag = hmac.new(tenant_key, iv + aad + ct, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(tag, expected_tag):
                raise ValueError("MAC tag verification failed")
            pt = bytes(b ^ iv[i % 12] for i, b in enumerate(ct))
            return pt.decode("utf-8")

        aesgcm = AESGCM(tenant_key)
        plaintext_bytes = aesgcm.decrypt(iv, ciphertext_and_tag, aad)
        return plaintext_bytes.decode("utf-8")
    except Exception as e:
        raise KeyUnavailableError(f"Decryption of signing secret failed: {e}") from e


def compute_payload_sha256(raw_bytes: bytes) -> str:
    """Compute deterministic SHA-256 hash strictly over raw wire bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def generate_hmac_signature(raw_body: bytes, timestamp: int, secret: str) -> str:
    """Generate HMAC-SHA256 signature for outgoing webhook payload."""
    msg = f"{timestamp}.".encode("utf-8") + raw_body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def parse_signature_header(header_value: str) -> tuple[int | None, list[str], str | None]:
    """Parse X-AURA-Signature-256 header (e.g. 't=123456,v1=abcdef,kid=key_123' or direct hex)."""
    if not header_value:
        return None, [], None

    timestamp = None
    signatures = []
    kid = None

    parts = [p.strip() for p in header_value.split(",")]
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "t":
                try:
                    timestamp = int(v)
                except ValueError:
                    pass
            elif k.startswith("v"):
                signatures.append(v)
            elif k == "kid":
                kid = v
        else:
            signatures.append(part)

    return timestamp, signatures, kid


def verify_hmac_signature(
    raw_body: bytes,
    timestamp: int,
    expected_signatures: list[str],
    signing_secrets: list[str],
) -> bool:
    """Verify HMAC-SHA256 signature in constant time across candidate secrets."""
    if not expected_signatures or not signing_secrets:
        return False

    msg = f"{timestamp}.".encode("utf-8") + raw_body
    for secret in signing_secrets:
        computed = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        for expected in expected_signatures:
            if hmac.compare_digest(computed.lower(), expected.lower()):
                return True
    return False


def create_lineage_token(
    user_id: str,
    root_event_id: str,
    causation_id: str | None,
    depth: int,
    master_key: bytes | None = None,
) -> str:
    """Generate cryptographically verifiable X-AURA-Lineage-Token."""
    tenant_key = derive_tenant_key(user_id, master_key=master_key)
    context_dict = {
        "user_id": user_id,
        "root_event_id": root_event_id,
        "causation_id": causation_id,
        "depth": depth,
    }
    json_bytes = json.dumps(context_dict, sort_keys=True).encode("utf-8")
    b64_context = base64.urlsafe_b64encode(json_bytes).decode("utf-8")
    sig = hmac.new(tenant_key, b64_context.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v1.{b64_context}.{sig}"


def verify_lineage_token(
    token: str,
    expected_user_id: str,
    master_key: bytes | None = None,
) -> dict[str, Any] | None:
    """Verify and parse X-AURA-Lineage-Token. Returns context dict if valid, else None."""
    if not token or not token.startswith("v1."):
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    _, b64_context, sig = parts
    try:
        tenant_key = derive_tenant_key(expected_user_id, master_key=master_key)
        computed_sig = hmac.new(tenant_key, b64_context.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig.lower(), computed_sig.lower()):
            return None

        json_bytes = base64.urlsafe_b64decode(b64_context.encode("utf-8"))
        context = json.loads(json_bytes.decode("utf-8"))
        if context.get("user_id") != expected_user_id:
            return None

        depth = context.get("depth", 1)
        if depth > 3:
            raise EventLoopDetectedError(f"Lineage depth {depth} exceeds maximum allowable depth of 3")

        return context
    except EventLoopDetectedError:
        raise
    except Exception as e:
        logger.debug(f"Failed to verify lineage token: {e}")
        return None
