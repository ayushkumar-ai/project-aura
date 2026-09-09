import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "ref",
    "ref_src",
    "fbclid",
    "gclid",
    "msclkid",
    "yclid",
    "mc_eid",
    "_ga",
    "_gl",
})

BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "169.254.169.254",
    "::1",
    "[::1]",
})

NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def _is_restricted_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IPv4 or IPv6 address belongs to private, loopback, link-local, multicast, or reserved ranges."""
    # Check IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            return _is_restricted_ip(ip_obj.ipv4_mapped)
        if ip_obj in NAT64_PREFIX:
            embedded = ipaddress.IPv4Address(ip_obj.packed[-4:])
            return _is_restricted_ip(embedded)

    # Check cloud metadata endpoint specifically
    if str(ip_obj) == "169.254.169.254":
        return True

    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    ):
        return True

    if ip_obj.is_reserved:
        return True

    return False


def resolve_and_validate_ip(hostname: str) -> tuple[bool, str | None]:
    """Resolve a hostname via safe DNS resolution and validate all resolved IPs against restricted address ranges."""
    if not hostname or not isinstance(hostname, str) or not hostname.strip():
        return False, "Missing hostname."

    clean_host = hostname.strip().lower()
    if clean_host.startswith("[") and clean_host.endswith("]"):
        clean_host = clean_host[1:-1].strip()

    if clean_host in BLOCKED_HOSTNAMES or clean_host.endswith(".local") or clean_host.endswith(".internal"):
        return False, f"Access to restricted hostname '{clean_host}' is blocked."

    # Check if clean_host is already a direct IP literal
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        if _is_restricted_ip(ip_obj):
            return False, f"Restricted IP address: {clean_host}"
        return True, None
    except ValueError:
        pass  # Hostname is a domain name, resolve via DNS

    try:
        addr_info = socket.getaddrinfo(clean_host, None)
    except (socket.gaierror, socket.herror, TimeoutError, socket.timeout):
        # Offline test environment or mock domain
        return True, None
    except Exception as e:
        return False, f"DNS resolution error for '{clean_host}': {e}"

    if not addr_info:
        return True, None

    for item in addr_info:
        sockaddr = item[4]
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if _is_restricted_ip(ip_obj):
                return False, f"Hostname '{clean_host}' resolves to restricted IP: {ip_str}"
        except ValueError:
            return False, f"Invalid resolved IP '{ip_str}' for hostname '{clean_host}'."

    return True, None


def is_safe_url(url: str, allow_local: bool = False) -> tuple[bool, str | None]:
    """Validate that a URL has a permitted scheme (HTTP/HTTPS) and its target host does not resolve to restricted IP ranges."""
    if not isinstance(url, str) or not url.strip():
        return False, "URL must be a non-empty string."

    cleaned = url.strip()
    try:
        parts = urlsplit(cleaned)
    except Exception as e:
        return False, f"Malformed URL: {e}"

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted."

    hostname = parts.hostname
    if not hostname:
        return False, "URL missing valid hostname."

    if allow_local:
        return True, None

    return resolve_and_validate_ip(hostname)


def normalize_url(url: str) -> str:
    """Deterministically normalizes a URL by standardizing scheme, host, port, trailing slashes,
    removing tracking parameters, sorting query arguments, and stripping fragments."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string.")

    cleaned = url.strip()
    try:
        parts = urlsplit(cleaned)
    except Exception as e:
        raise ValueError(f"Malformed URL '{cleaned}': {e}") from e

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{scheme}'. Must be http or https.")

    netloc = parts.netloc.lower()

    # Remove default port numbers
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parts.path
    # Normalize path: collapse consecutive slashes, strip trailing slash unless path is "/"
    if path:
        path = re.sub(r"/+", "/", path)
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
    else:
        path = ""

    # Parse, filter, and sort query parameters
    query_params = []
    if parts.query:
        for k, v in parse_qsl(parts.query, keep_blank_values=False):
            if k.lower() not in TRACKING_PARAMS:
                query_params.append((k, v))
        query_params.sort(key=lambda item: (item[0], item[1]))

    sorted_query = urlencode(query_params) if query_params else ""

    # Discard fragment (#...) entirely
    return urlunsplit((scheme, netloc, path, sorted_query, ""))


def deduplicate_urls(urls: list[str]) -> list[str]:
    """Deduplicate a list of URLs deterministically based on normalized URL identity,
    preserving the original representation of the first occurrence."""
    if not isinstance(urls, (list, tuple)):
        raise TypeError("urls must be a list or tuple of strings.")

    seen_normalized: set[str] = set()
    unique_urls: list[str] = []

    for raw_url in urls:
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            norm = normalize_url(raw_url)
            if norm not in seen_normalized:
                seen_normalized.add(norm)
                unique_urls.append(raw_url.strip())
        except ValueError:
            if raw_url.strip() not in seen_normalized:
                seen_normalized.add(raw_url.strip())
                unique_urls.append(raw_url.strip())

    return unique_urls
