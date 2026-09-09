import pytest
from research.models import SearchItem, WebDocument
from research.providers.fake import FakeFetchProvider, FakeSearchProvider, FakeWebProvider
from research.service import ResearchService


@pytest.fixture
def fake_web_setup():
    search_items = [
        SearchItem(title="Page 1", url="https://example.com/1", snippet="Snippet 1"),
        SearchItem(title="Page 2", url="https://example.com/2", snippet="Snippet 2"),
        SearchItem(title="Page 1 Duplicate", url="https://example.com/1", snippet="Snippet 1 Duplicate"),
        SearchItem(title="Page 3", url="https://example.com/3", snippet="Snippet 3"),
    ]
    docs = {
        "https://example.com/1": WebDocument(url="https://example.com/1", title="Doc 1", content="Detailed content 1"),
        "https://example.com/2": WebDocument(url="https://example.com/2", title="Doc 2", content="Detailed content 2"),
        "https://example.com/3": WebDocument(url="https://example.com/3", title="Doc 3", content="Detailed content 3"),
    }
    provider = FakeWebProvider(
        default_items=search_items,
        documents_by_url=docs,
    )
    service = ResearchService(
        search_provider=provider,
        fetch_provider=provider,
        max_search_results=5,
        max_fetch_sources=3,
        max_document_chars=500,
    )
    return {
        "provider": provider,
        "service": service,
        "search_items": search_items,
        "docs": docs,
    }


def test_research_service_search_deduplication(fake_web_setup):
    service = fake_web_setup["service"]
    res = service.search("test query")

    # Should deduplicate https://example.com/1
    urls = [itm.url for itm in res.items]
    assert len(urls) == 3
    assert urls == ["https://example.com/1", "https://example.com/2", "https://example.com/3"]


def test_research_service_bounds_enforcement(fake_web_setup):
    service = fake_web_setup["service"]

    # Request 10 sources, but max_fetch_sources is configured to 3
    report = service.research("test query", max_sources=10)

    assert len(report.sources) <= 3
    assert len(report.sources) == 3


def test_research_service_content_truncation():
    provider = FakeWebProvider(
        default_items=[SearchItem(title="Long Doc", url="https://example.com/long", snippet="snippet")],
        documents_by_url={
            "https://example.com/long": WebDocument(
                url="https://example.com/long",
                title="Long",
                content="A" * 200,
            )
        },
    )
    service = ResearchService(
        search_provider=provider,
        fetch_provider=provider,
        max_document_chars=50,
    )

    report = service.research("query")
    assert len(report.sources) == 1
    assert len(report.sources[0].content) <= 70  # 50 chars + truncation note
    assert "[truncated]" in report.sources[0].content


def test_research_service_fault_isolation_on_individual_fetch_failure():
    provider = FakeWebProvider(
        default_items=[
            SearchItem(title="Good 1", url="https://example.com/good1", snippet="s1"),
            SearchItem(title="Bad 1", url="https://example.com/bad", snippet="s2"),
            SearchItem(title="Good 2", url="https://example.com/good2", snippet="s3"),
        ],
        documents_by_url={
            "https://example.com/good1": WebDocument(url="https://example.com/good1", title="Good 1", content="C1"),
            "https://example.com/good2": WebDocument(url="https://example.com/good2", title="Good 2", content="C2"),
        },
        simulate_timeout_urls={"https://example.com/bad"},
    )
    service = ResearchService(
        search_provider=provider,
        fetch_provider=provider,
        max_fetch_sources=3,
    )

    report = service.research("query with failing source")

    # Fault isolation: Good sources succeed, Bad source is recorded in failed_sources without crashing
    assert len(report.sources) == 2
    assert len(report.failed_sources) == 1
    assert report.sources[0].url == "https://example.com/good1"
    assert report.sources[1].url == "https://example.com/good2"
    assert report.failed_sources[0].url == "https://example.com/bad"
    assert report.failed_sources[0].status == "failed"
    assert "timed out" in report.failed_sources[0].error


def test_research_service_search_provider_failure():
    provider = FakeSearchProvider(simulate_error=RuntimeError("Search backend down"))
    service = ResearchService(search_provider=provider)

    with pytest.raises(RuntimeError, match="Search provider error: Search backend down"):
        service.search("query")


def test_research_service_empty_results():
    provider = FakeSearchProvider(default_items=[])
    service = ResearchService(search_provider=provider)

    report = service.research("empty results query")
    assert len(report.sources) == 0
    assert len(report.failed_sources) == 0
    assert report.summary == "No search results found."
