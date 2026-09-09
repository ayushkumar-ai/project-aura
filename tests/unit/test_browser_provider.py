import json
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from research.interfaces import BrowserProvider
from research.models import WebDocument
from research.providers.browser import BrowserFetchProvider, FakeBrowserProvider
from research.providers.factory import create_browser_provider


def test_fake_browser_provider_retrieval():
    docs = {
        "https://spa.example.com/app": WebDocument(
            url="https://spa.example.com/app",
            title="SPA Application",
            content="Dynamic React rendered content.",
        )
    }
    provider = FakeBrowserProvider(rendered_documents_by_url=docs)
    assert isinstance(provider, BrowserProvider)
    assert provider.name == "FakeBrowserProvider"

    doc = provider.fetch_page("https://spa.example.com/app")
    assert doc.is_success is True
    assert doc.title == "SPA Application"
    assert doc.content == "Dynamic React rendered content."
    assert len(provider.recorded_fetches) == 1


def test_browser_fetch_provider_successful_page_render():
    provider = BrowserFetchProvider(default_timeout=5.0, max_document_chars=500)
    assert provider.name == "BrowserFetchProvider"

    canned_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Dynamic Portal</title></head>
      <body>
        <main>
          <h1>Portal Header</h1>
          <p>This is rendered visible body content.</p>
        </main>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = "https://portal.example.com/home"
    mock_resp.status = 200
    mock_resp.headers.get_content_type.return_value = "text/html"
    mock_resp.headers.get_content_charset.return_value = "utf-8"
    mock_resp.read.return_value = canned_html.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("research.providers.browser._is_safe_host", return_value=(True, None)):
        doc = provider.fetch_page("https://portal.example.com/home")

    assert doc.is_success is True
    assert doc.title == "Dynamic Portal"
    assert "Portal Header" in doc.content
    assert "This is rendered visible body content." in doc.content
    assert doc.metadata.get("rendered") is True


def test_browser_fetch_provider_ssrf_blocking():
    provider = BrowserFetchProvider()

    # Loopback IP
    doc = provider.fetch_page("http://127.0.0.1:8080/admin")
    assert doc.is_success is False
    assert doc.status_code == 403
    assert "SSRF Protection" in doc.error

    # Localhost
    doc = provider.fetch_page("http://localhost/secret")
    assert doc.is_success is False
    assert doc.status_code == 403
    assert "SSRF Protection" in doc.error


def test_browser_fetch_provider_scheme_rejection():
    provider = BrowserFetchProvider()
    with pytest.raises(ValueError, match="Browser only supports http:// and https://"):
        provider.fetch_page("file:///C:/Windows/System32/drivers/etc/hosts")

    with pytest.raises(ValueError, match="Browser only supports http:// and https://"):
        provider.fetch_page("ftp://files.example.com/data.tar")


def test_browser_fetch_provider_timeout_handling():
    provider = BrowserFetchProvider(default_timeout=0.1)

    with patch("research.providers.browser._is_safe_host", return_value=(True, None)), \
         patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
        doc = provider.fetch_page("https://slow.example.com")

    assert doc.is_success is False
    assert doc.status_code == 504
    assert "timed out" in doc.error.lower()


def test_browser_fetch_provider_content_truncation():
    provider = BrowserFetchProvider(max_document_chars=50)

    canned_html = "<html><head><title>Large</title></head><body>" + "<p>Passage</p>" * 50 + "</body></html>"
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = "https://large.example.com"
    mock_resp.status = 200
    mock_resp.headers.get_content_type.return_value = "text/html"
    mock_resp.headers.get_content_charset.return_value = "utf-8"
    mock_resp.read.return_value = canned_html.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("research.providers.browser._is_safe_host", return_value=(True, None)):
        doc = provider.fetch_page("https://large.example.com")

    assert doc.is_success is True
    assert len(doc.content) <= 70  # 50 chars + truncation marker
    assert "[truncated]" in doc.content


def test_create_browser_provider_factory():
    fake = create_browser_provider("fake")
    assert isinstance(fake, FakeBrowserProvider)

    browser = create_browser_provider("browser", timeout=20.0, max_document_chars=5000)
    assert isinstance(browser, BrowserFetchProvider)
    assert browser.default_timeout == 20.0
    assert browser.max_document_chars == 5000

    with pytest.raises(ValueError, match="Unsupported browser provider"):
        create_browser_provider("selenium_grid")
