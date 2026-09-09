import pytest
from research.models import SearchItem, WebDocument
from research.providers.browser import FakeBrowserProvider
from research.providers.fake import FakeFetchProvider, FakeSearchProvider, FakeWebProvider
from research.service import ResearchService


def test_research_service_fetch_dynamic():
    search_provider = FakeSearchProvider(default_items=[])
    browser_provider = FakeBrowserProvider(
        rendered_documents_by_url={
            "https://dashboard.example.com": WebDocument(
                url="https://dashboard.example.com",
                title="Live Dashboard",
                content="Active nodes: 42. Cluster health: 100%.",
            )
        }
    )
    service = ResearchService(
        search_provider=search_provider,
        browser_provider=browser_provider,
    )

    doc = service.fetch_dynamic("https://dashboard.example.com")
    assert doc.is_success is True
    assert doc.title == "Live Dashboard"
    assert "Active nodes: 42" in doc.content


def test_research_service_dynamic_research_workflow():
    search_items = [
        SearchItem(title="SPA Frameworks", url="https://spa.dev/status", snippet="Frontend framework metrics."),
    ]
    search_provider = FakeSearchProvider(default_items=search_items)
    browser_provider = FakeBrowserProvider(
        rendered_documents_by_url={
            "https://spa.dev/status": WebDocument(
                url="https://spa.dev/status",
                title="SPA Frameworks Status",
                content="Next.js and SvelteKit show 98% satisfaction rating in 2026.",
            )
        }
    )
    service = ResearchService(
        search_provider=search_provider,
        browser_provider=browser_provider,
    )

    report = service.research("framework satisfaction", use_dynamic=True)
    assert len(report.sources) == 1
    assert report.sources[0].url == "https://spa.dev/status"
    assert "98% satisfaction" in report.sources[0].content
    assert report.metadata.get("dynamic_requested") is True


def test_research_service_automatic_dynamic_fallback():
    # Static fetch provider returns empty/minimal placeholder content
    search_items = [
        SearchItem(title="Client App", url="https://app.io", snippet="App"),
    ]
    fetch_provider = FakeFetchProvider(
        documents_by_url={
            "https://app.io": WebDocument(
                url="https://app.io",
                title="App",
                content="",  # Empty static content triggers dynamic fallback
            )
        }
    )
    browser_provider = FakeBrowserProvider(
        rendered_documents_by_url={
            "https://app.io": WebDocument(
                url="https://app.io",
                title="Client App Rendered",
                content="Fully rendered dynamic dashboard with charts and real-time statistics.",
            )
        }
    )
    service = ResearchService(
        search_provider=FakeSearchProvider(default_items=search_items),
        fetch_provider=fetch_provider,
        browser_provider=browser_provider,
    )

    report = service.research("app statistics", use_dynamic=False)
    assert len(report.sources) == 1
    # Successfully upgraded from empty static doc to browser-rendered doc
    assert "Fully rendered dynamic dashboard" in report.sources[0].content


def test_research_service_browser_unconfigured_error():
    service = ResearchService(search_provider=FakeSearchProvider(default_items=[]))
    with pytest.raises(RuntimeError, match="Browser provider is not configured"):
        service.fetch_dynamic("https://example.com")
