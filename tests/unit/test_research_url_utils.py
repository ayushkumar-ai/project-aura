import pytest
from research.url_utils import deduplicate_urls, normalize_url


def test_normalize_url_basic_standardization():
    raw = "HTTPS://Example.COM:443/Path/To/Doc/?b=2&a=1#section"
    normalized = normalize_url(raw)
    assert normalized == "https://example.com/Path/To/Doc?a=1&b=2"


def test_normalize_url_removes_default_ports():
    assert normalize_url("http://example.com:80/page") == "http://example.com/page"
    assert normalize_url("https://example.com:443/page") == "https://example.com/page"
    assert normalize_url("http://example.com:8080/page") == "http://example.com:8080/page"


def test_normalize_url_strips_tracking_parameters():
    raw = "https://example.com/article?utm_source=twitter&utm_medium=social&utm_campaign=spring&id=123&ref=feed"
    normalized = normalize_url(raw)
    assert normalized == "https://example.com/article?id=123"


def test_normalize_url_strips_trailing_slash():
    assert normalize_url("https://example.com/docs/") == "https://example.com/docs"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_url_rejects_invalid_scheme():
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        normalize_url("ftp://example.com/file.txt")
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        normalize_url("file:///C:/secrets.txt")


def test_deduplicate_urls_preserves_first_occurrence():
    urls = [
        "https://example.com/page?utm_source=tw",
        "https://example.com/other",
        "https://EXAMPLE.com/page?ref=site#frag",
        "https://example.com/other/",
    ]
    deduped = deduplicate_urls(urls)
    assert len(deduped) == 2
    assert deduped == [
        "https://example.com/page?utm_source=tw",
        "https://example.com/other",
    ]
