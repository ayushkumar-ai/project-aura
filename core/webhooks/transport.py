"""M54 — Outbound Webhook Transport, SSRF Validation & Pinned IP Socket Execution."""

from __future__ import annotations

import http.client
import ipaddress
import logging
import random
import socket
import ssl
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from core.webhooks.types import (
    SSRFViolationError,
    SSRFMultiAddressViolationError,
    InvalidWebhookPortError,
)

logger = logging.getLogger("aura.webhooks.transport")

# Forbidden CIDRs for SSRF protection
FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("2001:db8::/32"),
]

# Explicit cloud metadata hostnames / IPs
CLOUD_METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}
FORBIDDEN_HOSTNAMES = {"localhost", "metadata.google.internal", "instance-data", "metadata"}


def is_ip_allowed(ip_str: str) -> bool:
    """Check if single IP address is safe and not in forbidden private/loopback/metadata ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if ip_str in CLOUD_METADATA_IPS:
        return False

    # Check for IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_ip_allowed(str(ip.ipv4_mapped))

    for net in FORBIDDEN_NETWORKS:
        if ip in net:
            return False

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False

    return True


def validate_outbound_target(target_url: str) -> tuple[str, int, list[str]]:
    """Validate target URL: scheme must be HTTPS, port within 443..8443, and ALL resolved IPs safe."""
    parsed = urllib.parse.urlparse(target_url)
    if parsed.scheme.lower() != "https":
        raise SSRFViolationError(f"Only HTTPS scheme is permitted for outbound webhooks, got '{parsed.scheme}'")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFViolationError(f"Target URL '{target_url}' missing valid hostname")

    if hostname.lower() in FORBIDDEN_HOSTNAMES:
        raise SSRFViolationError(f"Hostname '{hostname}' is explicitly prohibited (cloud metadata / loopback)")

    port = parsed.port or 443
    if not (443 <= port <= 8443):
        raise InvalidWebhookPortError(f"Target port {port} is outside authorized range 443..8443")

    # If hostname is a literal IP
    try:
        ipaddress.ip_address(hostname)
        if not is_ip_allowed(hostname):
            raise SSRFViolationError(f"Direct IP target '{hostname}' is in forbidden range")
        return hostname, port, [hostname]
    except ValueError:
        pass

    # Resolve DNS
    try:
        addr_info = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFViolationError(f"DNS resolution failed for hostname '{hostname}': {e}") from e

    resolved_ips = list(dict.fromkeys(info[4][0] for info in addr_info if info[4]))
    if not resolved_ips:
        raise SSRFViolationError(f"No IP addresses resolved for hostname '{hostname}'")

    # Conservative Multi-IP Validation: ALL resolved IPs must pass!
    for ip_str in resolved_ips:
        if not is_ip_allowed(ip_str):
            raise SSRFMultiAddressViolationError(
                f"Multi-IP DNS check failed: Hostname '{hostname}' resolved to prohibited IP '{ip_str}'"
            )

    return hostname, port, resolved_ips


class PinnedIPTransport:
    """Dedicated HTTPS transport that connects directly to a validated pinned IP, bypassing all proxies."""

    def __init__(self, pinned_ip: str, hostname: str, port: int = 443, timeout: float = 10.0):
        if not (443 <= port <= 8443):
            raise InvalidWebhookPortError(f"Webhook port {port} is outside authorized range 443..8443")
        self.pinned_ip = pinned_ip
        self.hostname = hostname
        self.port = port
        self.timeout = timeout

    def send_request(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute HTTPS request connecting directly to pinned IP socket, setting TLS SNI to hostname."""
        # Open raw socket connection directly to pinned_ip, ignoring all proxies
        sock = socket.create_connection((self.pinned_ip, self.port), timeout=self.timeout)

        # Wrap with TLS
        ssl_ctx = ssl.create_default_context()
        tls_sock = ssl_ctx.wrap_socket(sock, server_hostname=self.hostname)

        # Use http.client over the established TLS socket
        conn = http.client.HTTPSConnection(
            self.hostname,
            port=self.port,
            timeout=self.timeout,
        )
        conn.sock = tls_sock

        try:
            req_headers = dict(headers)
            req_headers["Host"] = self.hostname
            req_headers["Content-Length"] = str(len(body))
            req_headers["Connection"] = "close"

            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()

            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            # Cap read body to 100KB
            resp_body = resp.read(102400)
            return status, resp_headers, resp_body
        finally:
            conn.close()


def parse_retry_after_header(header_val: str | None, max_seconds: float = 3600.0) -> float | None:
    """Parse HTTP Retry-After header as integer seconds and clamp to max_seconds."""
    if not header_val or not str(header_val).strip():
        return None
    try:
        val = float(header_val.strip())
        if val <= 0:
            return None
        return min(max_seconds, val)
    except (ValueError, TypeError):
        return None


def calculate_backoff_delay(
    attempt: int,
    retry_after_header: str | None = None,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
) -> float:
    """Calculate exponential backoff delay with full jitter, honoring clamped Retry-After."""
    if retry_after_header:
        parsed_after = parse_retry_after_header(retry_after_header, max_seconds=3600.0)
        if parsed_after is not None:
            return parsed_after

    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return random.uniform(0.1, max(0.1, ceiling))
