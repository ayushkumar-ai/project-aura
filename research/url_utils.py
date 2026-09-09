import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Standard marketing / tracking query parameters to discard during normalization
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
