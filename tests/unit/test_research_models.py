import pytest
from dataclasses import FrozenInstanceError

from research.models import (
    ResearchReport,
    ResearchSource,
    SearchItem,
    SearchResult,
    WebDocument,
)


def test_search_item_valid_initialization():
    item = SearchItem(
        title="Test Page",
        url="https://example.com/test",
        snippet="This is a test snippet.",
    )
    assert item.title == "Test Page"
    assert item.url == "https://example.com/test"
    assert item.snippet == "This is a test snippet."
    assert item.source_domain == "example.com"
    assert item.metadata == {}


def test_search_item_rejects_invalid_url():
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        SearchItem(title="Test", url="ftp://example.com", snippet="snippet")

    with pytest.raises(ValueError, match="Invalid URL scheme"):
        SearchItem(title="Test", url="invalid-url", snippet="snippet")

    with pytest.raises(ValueError, match="url must be a non-empty string"):
        SearchItem(title="Test", url="   ", snippet="snippet")


def test_search_item_rejects_empty_title():
    with pytest.raises(ValueError, match="title must be a non-empty string"):
        SearchItem(title="", url="https://example.com", snippet="snippet")


def test_search_item_immutability():
    item = SearchItem(title="Test", url="https://example.com", snippet="s")
    with pytest.raises(FrozenInstanceError):
        item.title = "New Title"  # type: ignore


def test_search_item_rejects_callable_in_metadata():
    def malicious_func():
        pass

    with pytest.raises(ValueError, match="cannot be callable"):
        SearchItem(
            title="Test",
            url="https://example.com",
            snippet="s",
            metadata={"func": malicious_func},
        )


def test_search_result_valid_and_immutable():
    item = SearchItem(title="Item 1", url="https://example.com/1", snippet="snip")
    res = SearchResult(query="test query", items=(item,), total_results=1)

    assert res.query == "test query"
    assert len(res.items) == 1
    assert res.total_results == 1

    with pytest.raises(FrozenInstanceError):
        res.query = "changed"  # type: ignore


def test_search_result_rejects_invalid_items():
    with pytest.raises(TypeError, match="SearchItem instances"):
        SearchResult(query="test", items=["not an item"])  # type: ignore


def test_web_document_status_and_validation():
    doc = WebDocument(
        url="https://example.com/page",
        title="Page Title",
        content="Page body content",
        status_code=200,
    )
    assert doc.is_success is True
    assert doc.error is None

    failed_doc = WebDocument(
        url="https://example.com/404",
        status_code=404,
        error="Not Found",
    )
    assert failed_doc.is_success is False


def test_research_source_and_report_citations():
    src1 = ResearchSource(
        url="https://example.com/a",
        title="Source A",
        content="Content A",
        snippet="Snippet A",
    )
    src2 = ResearchSource(
        url="https://example.com/b",
        title="Source B",
        content="Content B",
        snippet="Snippet B",
    )
    failed_src = ResearchSource(
        url="https://example.com/c",
        title="Source C",
        status="failed",
        error="Timeout",
    )

    report = ResearchReport(
        query="quantum computing",
        sources=(src1, src2),
        failed_sources=(failed_src,),
    )

    assert report.has_sources is True
    assert len(report.sources) == 2
    assert len(report.failed_sources) == 1

    citations = report.format_citations()
    assert "[1] Source A - https://example.com/a" in citations
    assert "[2] Source B - https://example.com/b" in citations
