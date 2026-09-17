"""M54 — Cryptography, HMAC Verification, and Lineage Protocol Unit Tests."""

import json
import time
import pytest
from datetime import datetime
from core.webhooks.crypto import (
    derive_tenant_key,
    encrypt_signing_secret,
    decrypt_signing_secret,
    compute_payload_sha256,
    generate_hmac_signature,
    parse_signature_header,
    verify_hmac_signature,
    create_lineage_token,
    verify_lineage_token,
)
from core.webhooks.types import (
    WebhookSignatureError,
    WebhookTimestampError,
    EventLoopDetectedError,
)


class TestM54CryptoAndSecrets:
    """Category 1 & Category 5.1 tests: AES-256-GCM, HKDF, Dual-Key Rotation."""

    def test_tenant_key_derivation_deterministic_and_isolated(self):
        """HKDF derivation produces unique 32-byte keys isolated per tenant."""
        key_tenant_a = derive_tenant_key("tenant_a")
        key_tenant_a_dup = derive_tenant_key("tenant_a")
        key_tenant_b = derive_tenant_key("tenant_b")

        assert len(key_tenant_a) == 32
        assert len(key_tenant_b) == 32
        assert key_tenant_a == key_tenant_a_dup
        assert key_tenant_a != key_tenant_b

    def test_tenant_key_derivation_unicode_and_special_chars(self):
        """Key derivation functions correctly with UTF-8 tenant identifiers and UUIDs."""
        u1 = derive_tenant_key("tenant-uuid-1234-5678-90ab")
        u2 = derive_tenant_key("tenant_éàü_user")
        assert len(u1) == 32
        assert len(u2) == 32
        assert u1 != u2

    def test_aes_256_gcm_envelope_encryption_roundtrip(self):
        """Plaintext secrets are encrypted with AES-256-GCM with distinct IVs."""
        secret = "whsec_super_secret_signing_key_12345"

        enc1 = encrypt_signing_secret(secret, "ep_1", "tenant_a")
        enc2 = encrypt_signing_secret(secret, "ep_1", "tenant_a")

        # Nonce is randomly generated, so ciphertext must differ
        assert enc1["iv"] != enc2["iv"]
        assert enc1["ciphertext"] != enc2["ciphertext"]

        dec1 = decrypt_signing_secret(enc1, "ep_1", "tenant_a")
        dec2 = decrypt_signing_secret(enc2, "ep_1", "tenant_a")

        assert dec1 == secret
        assert dec2 == secret

    def test_aes_256_gcm_tamper_detection(self):
        """Tampered ciphertext fails AES-GCM authentication tag verification."""
        secret = "whsec_sensitive_webhook_secret_value"
        enc = encrypt_signing_secret(secret, "ep_1", "tenant_a")

        tampered = dict(enc)
        ct = enc["ciphertext"]
        tampered["ciphertext"] = ct[:-4] + ("AAAA" if ct[-4:] != "AAAA" else "BBBB")

        with pytest.raises(Exception):
            decrypt_signing_secret(tampered, "ep_1", "tenant_a")

    def test_aes_256_gcm_invalid_iv_or_tag_structure(self):
        """Malformed envelope payload missing iv or tag raises ValueError."""
        with pytest.raises(Exception):
            decrypt_signing_secret({"iv": "invalid_b64", "ciphertext": "abc", "tag": "def"}, "ep_1", "tenant_a")

    def test_aes_256_gcm_cross_tenant_decryption_failure(self):
        """Ciphertext encrypted for tenant_a cannot be decrypted by tenant_b (AAD mismatch)."""
        secret = "whsec_cross_tenant_test"
        enc = encrypt_signing_secret(secret, "ep_1", "tenant_a")

        with pytest.raises(Exception):
            decrypt_signing_secret(enc, "ep_1", "tenant_b")

    def test_aes_256_gcm_cross_endpoint_decryption_failure(self):
        """Ciphertext encrypted for endpoint_1 cannot be decrypted by endpoint_2 (AAD mismatch)."""
        secret = "whsec_cross_endpoint_test"
        enc = encrypt_signing_secret(secret, "ep_1", "tenant_a")

        with pytest.raises(Exception):
            decrypt_signing_secret(enc, "ep_2", "tenant_a")


class TestM54HmacSignatures:
    """Category 1 tests: HMAC-SHA256, Dual-Key Verification, Timestamp tolerance."""

    def test_hmac_signature_generation_and_constant_time_verification(self):
        """HMAC-SHA256 verification succeeds with exact raw body and valid timestamp."""
        body = b'{"event": "task.completed", "task_id": "tsk_123"}'
        timestamp = int(time.time())
        secret = "whsec_test_secret_12345"

        sig = generate_hmac_signature(body, timestamp, secret)
        assert len(sig) == 64

        assert verify_hmac_signature(body, timestamp, [sig], [secret]) is True
        assert verify_hmac_signature(body, timestamp, ["bad" + sig[3:]], [secret]) is False
        assert verify_hmac_signature(body, timestamp + 1, [sig], [secret]) is False
        assert verify_hmac_signature(body + b" ", timestamp, [sig], [secret]) is False

    def test_dual_key_rotation_verification(self):
        """Inbound signature matches either active primary or retiring secondary key."""
        body = b'{"event": "order.created"}'
        ts = int(time.time())
        old_secret = "whsec_old_retiring_secret"
        new_secret = "whsec_new_primary_secret"

        sig_old = generate_hmac_signature(body, ts, old_secret)
        sig_new = generate_hmac_signature(body, ts, new_secret)

        candidates = [new_secret, old_secret]
        assert verify_hmac_signature(body, ts, [sig_old], candidates) is True
        assert verify_hmac_signature(body, ts, [sig_new], candidates) is True
        assert verify_hmac_signature(body, ts, ["invalid_sig"], candidates) is False

    def test_parse_signature_header_formats(self):
        """Parses standard Stripe/GitHub/AURA signature header formats."""
        header = "t=1700000000,v1=abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890,kid=key_primary_1"
        ts, sigs, kid = parse_signature_header(header)
        assert ts == 1700000000
        assert "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890" in sigs
        assert kid == "key_primary_1"

    def test_parse_signature_header_multiple_signatures(self):
        """Parses multiple v1 signatures in header."""
        header = "t=1700000000,v1=sig_alpha,v1=sig_beta"
        ts, sigs, kid = parse_signature_header(header)
        assert ts == 1700000000
        assert "sig_alpha" in sigs
        assert "sig_beta" in sigs
        assert kid is None

    def test_parse_signature_header_malformed_variations(self):
        """Gracefully handles malformed header strings."""
        ts, sigs, kid = parse_signature_header("invalid_garbage_header")
        assert ts is None
        assert "invalid_garbage_header" in sigs
        assert kid is None

    def test_compute_payload_sha256(self):
        """Canonical SHA-256 computation over exact raw bytes."""
        body = b'{"hello": "world"}'
        h1 = compute_payload_sha256(body)
        h2 = compute_payload_sha256(b'{"hello": "world"}')
        h3 = compute_payload_sha256(b'{"hello":"world"}')

        assert len(h1) == 64
        assert h1 == h2
        assert h1 != h3

    def test_compute_payload_sha256_empty_bytes(self):
        """SHA-256 of empty bytes matches e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855."""
        h = compute_payload_sha256(b"")
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestM54LineageAndLoopDefense:
    """Category 4 tests: Cryptographic Lineage Protocol & Event Loop Defense."""

    def test_lineage_token_generation_and_verification(self):
        """Lineage tokens create tamper-proof hop chains with depth tracking."""
        token = create_lineage_token(
            user_id="tenant_123",
            root_event_id="evt_root_1",
            causation_id="evt_hop_1",
            depth=1,
        )
        assert token.startswith("v1.")
        assert token.count(".") == 2

        ctx = verify_lineage_token(token, "tenant_123")
        assert ctx is not None
        assert ctx["root_event_id"] == "evt_root_1"
        assert ctx["causation_id"] == "evt_hop_1"
        assert ctx["depth"] == 1

    def test_lineage_token_tamper_rejected(self):
        """Tampered lineage token signature returns None."""
        token = create_lineage_token(
            user_id="tenant_123",
            root_event_id="evt_root_1",
            causation_id="evt_hop_1",
            depth=1,
        )
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1] + "tamper." + parts[2]

        assert verify_lineage_token(tampered, "tenant_123") is None

    def test_lineage_token_cross_tenant_rejected(self):
        """Lineage token for tenant_a returns None when verified by tenant_b."""
        token = create_lineage_token(
            user_id="tenant_a",
            root_event_id="evt_root_1",
            causation_id="evt_hop_1",
            depth=1,
        )
        assert verify_lineage_token(token, "tenant_b") is None

    def test_lineage_token_max_depth_exceeded_raises_loop_error(self):
        """Exceeding max_depth (> 3) raises EventLoopDetectedError."""
        token = create_lineage_token(
            user_id="tenant_123",
            root_event_id="evt_root_1",
            causation_id="evt_hop_5",
            depth=4,
        )
        with pytest.raises(EventLoopDetectedError):
            verify_lineage_token(token, "tenant_123")
