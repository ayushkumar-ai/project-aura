import io
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from research.providers.http_fetch import HttpFetchProvider


def test_http_fetch_rejects_unsupported_schemes():
    provider = HttpFetchProvider()

    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        provider.fetch("ftp://example.com/file.txt")

    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        provider.fetch("file:///etc/passwd")


def test_http_fetch_blocks_ssrf_local_targets():
    provider = HttpFetchProvider(allow_local=False)

    doc_local = provider.fetch("http://localhost:8080/admin")
    assert doc_local.is_success is False
    assert doc_local.status_code == 403
    assert "Access to local or private network targets is restricted." in doc_local.error

    doc_ip = provider.fetch("http://127.0.0.1:5000/keys")
    assert doc_ip.is_success is False
    assert doc_ip.status_code == 403

    doc_metadata = provider.fetch("http://169.254.169.254/latest/meta-data")
    assert doc_metadata.is_success is False
    assert doc_metadata.status_code == 403

    doc_private_ip = provider.fetch("http://192.168.1.1/router")
    assert doc_private_ip.is_success is False
    assert doc_private_ip.status_code == 403


def test_http_fetch_successful_html_extraction():
    provider = HttpFetchProvider(allow_local=True)

    fake_html = b"""
    <html>
        <head><title>Research Article</title></head>
        <body>
            <h1>Quantum Breakthrough</h1>
            <p>Researchers built 10,000 logical qubits.</p>
        </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers.get.return_value = "text/html; charset=utf-8"
    mock_resp.headers.get_content_charset.return_value = "utf-8"
    mock_resp.read.side_effect = [fake_html, b""]
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        doc = provider.fetch("https://example.com/quantum")

    assert doc.is_success is True
    assert doc.title == "Research Article"
    assert "Researchers built 10,000 logical qubits." in doc.content


def test_http_fetch_rejects_binary_content():
    provider = HttpFetchProvider(allow_local=True)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers.get.return_value = "image/png"
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        doc = provider.fetch("https://example.com/image.png")

    assert doc.is_success is False
    assert doc.status_code == 415
    assert "Unsupported binary content type" in doc.error


def test_http_fetch_handles_http_errors():
    provider = HttpFetchProvider(allow_local=True)

    http_err = urllib.error.HTTPError(
        url="https://example.com/404",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b"Not Found"),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        doc = provider.fetch("https://example.com/404")

    assert doc.is_success is False
    assert doc.status_code == 404
    assert "404" in doc.error


def test_http_fetch_handles_timeout():
    provider = HttpFetchProvider(allow_local=True)

    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        doc = provider.fetch("https://example.com/slow")

    assert doc.is_success is False
    assert doc.status_code == 504
    assert "timed out" in doc.error
