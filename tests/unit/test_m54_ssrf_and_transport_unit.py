"""M54 — SSRF Defense, Multi-IP DNS Validation, and Transport Unit Tests."""

import socket
import pytest
from core.webhooks.transport import (
    validate_outbound_target,
    calculate_backoff_delay,
    parse_retry_after_header,
    PinnedIPTransport,
)
from core.webhooks.types import (
    SSRFViolationError,
    SSRFMultiAddressViolationError,
    InvalidWebhookPortError,
)


class TestM54SSRFValidation:
    """Category 5 tests: SSRF multi-IP DNS validation, port constraints [443..8443]."""

    def test_reject_non_https_scheme(self):
        """Plain HTTP or other schemes are immediately rejected."""
        with pytest.raises(SSRFViolationError, match="HTTPS"):
            validate_outbound_target("http://example.com/webhook")
        with pytest.raises(SSRFViolationError, match="HTTPS"):
            validate_outbound_target("ftp://example.com/webhook")
        with pytest.raises(SSRFViolationError, match="HTTPS"):
            validate_outbound_target("file:///etc/passwd")

    def test_reject_disallowed_ports(self):
        """Ports outside [443..8443] or standard HTTP ports like 80 are rejected."""
        with pytest.raises(InvalidWebhookPortError):
            validate_outbound_target("https://example.com:80/webhook")
        with pytest.raises(InvalidWebhookPortError):
            validate_outbound_target("https://example.com:22/webhook")
        with pytest.raises(InvalidWebhookPortError):
            validate_outbound_target("https://example.com:442/webhook")
        with pytest.raises(InvalidWebhookPortError):
            validate_outbound_target("https://example.com:8444/webhook")
        with pytest.raises(InvalidWebhookPortError):
            validate_outbound_target("https://example.com:9000/webhook")

    def test_allow_valid_ports_in_range(self, monkeypatch):
        """Ports 443, 8443, 8000 (within 443..8443) are allowed for public IPs."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ])

        hostname, port, resolved_ips = validate_outbound_target("https://api.example.com:8443/webhook")
        assert hostname == "api.example.com"
        assert port == 8443
        assert "93.184.216.34" in resolved_ips

    def test_reject_loopback_and_private_ips(self, monkeypatch):
        """Private RFC-1918, loopback 127.0.0.1, and link-local addresses are rejected."""
        test_ips = [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # AWS/GCP metadata IP
            "::1",
            "fc00::1",
            "fe80::1",
        ]
        for ip in test_ips:
            monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, ip_val=ip, **kwargs: [
                (socket.AF_INET if ":" not in ip_val else socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip_val, port))
            ])
            with pytest.raises(SSRFViolationError):
                validate_outbound_target("https://evil.internal.local/webhook")

    def test_conservative_multi_ip_dns_rejection(self, monkeypatch):
        """If hostname resolves to multiple IPs and ANY IP is private, reject entire target."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ])

        with pytest.raises(SSRFMultiAddressViolationError):
            validate_outbound_target("https://split-horizon.attack.com/webhook")

    def test_multi_ip_dns_all_public_allowed_and_pinned(self, monkeypatch):
        """If all resolved IPs are public, first resolved IP is pinned."""
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", port)),
        ])

        hostname, port, resolved_ips = validate_outbound_target("https://cdn.example.com/webhook")
        assert resolved_ips[0] == "93.184.216.34"
        assert len(resolved_ips) == 2


class TestM54TransportAndBackoff:
    """Category 5 & 6 tests: Jittered backoff, attempt bounds, Retry-After."""

    def test_calculate_backoff_delay_exponential_with_jitter_and_cap(self):
        """Backoff delay follows base * (2^(attempt-1)) + jitter, bounded by backoff_max."""
        for attempt in range(1, 10):
            delay = calculate_backoff_delay(attempt, base_seconds=1.0, max_seconds=60.0)
            assert delay >= 0.1
            assert delay <= 65.0  # max 60 + jitter

    def test_parse_retry_after_header_integer(self):
        """Parses integer Retry-After header and clamps to max."""
        assert parse_retry_after_header("120", max_seconds=3600) == 120.0
        assert parse_retry_after_header("7200", max_seconds=3600) == 3600.0
        assert parse_retry_after_header("-10", max_seconds=3600) is None
        assert parse_retry_after_header("invalid_str", max_seconds=3600) is None
